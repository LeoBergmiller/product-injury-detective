"""Build safer_incidents and safer_codebook Parquet (docs/SCHEMA.md) from raw pulls.

Inputs: pages and lookups written by build/fetch_saferproducts_incidents.py.
- WCF dates "/Date(ms)/" are converted to DATE as UTC (all observed: midnight UTC).
- VictimAgeInMonths 0 -> NULL (unknown); -1 ("Unspecified", or undefined for sex)
  -> NULL in the configured id columns.
- NOTE: SaferProducts sex ids are 1 = Female, 2 = Male, 3 = Unknown, which is the
  OPPOSITE of NEISS (1 = Male, 2 = Female). Always join safer_codebook for labels.

Usage: uv run python build/build_saferproducts.py [--config build/config.yaml]
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import yaml

LOG = logging.getLogger("build_saferproducts")
REPO_ROOT = Path(__file__).resolve().parent.parent

# raw field -> output column, in docs/SCHEMA.md order
FIELDS = [
    ("IncidentReportId", "incident_report_id"),
    ("IncidentReportNumber", "incident_report_number"),
    ("IncidentReportDate", "report_date"),
    ("IncidentReportPublicationDate", "publication_date"),
    ("IncidentDate", "incident_date"),
    ("ProductCategoryId", "sp_category_id"),
    ("IncidentProductDescription", "product_description"),
    ("ProductBrandName", "brand"),
    ("ProductModelName", "model"),
    ("ProductManufacturerName", "manufacturer_name"),
    ("ProductRetailCompanyName", "retailer_name"),
    ("IncidentDescription", "description"),
    ("SeverityTypeId", "severity_type_id"),
    ("LocaleId", "locale_id"),
    ("VictimGenderId", "victim_sex_id"),
    ("VictimAgeInMonths", "victim_age_months"),
    ("SourceTypeId", "source_type_id"),
]


@dataclass(frozen=True)
class BuildConfig:
    pages_dir: Path
    lookups_dir: Path
    tmp_dir: Path
    out_dir: Path
    expected_rows_min: int
    minus_one_to_null: list[str]
    age_bands: list[tuple[int, int | None, str]]
    lookups: dict[str, list[str]]

    @classmethod
    def from_yaml(cls, path: Path) -> BuildConfig:
        raw = yaml.safe_load(path.read_text())
        b = raw["saferproducts_build"]
        return cls(
            pages_dir=REPO_ROOT / b["pages_dir"],
            lookups_dir=REPO_ROOT / b["lookups_dir"],
            tmp_dir=REPO_ROOT / b["tmp_dir"],
            out_dir=REPO_ROOT / b["out_dir"],
            expected_rows_min=int(b["expected_rows_min"]),
            minus_one_to_null=list(b["minus_one_to_null"]),
            age_bands=[(int(lo), None if hi is None else int(hi), lab)
                       for lo, hi, lab in raw["neiss"]["age_bands"]],
            lookups={k: list(v)
                     for k, v in raw["saferproducts_incidents"]["lookups"].items()},
        )


def odata_rows(path: Path) -> list[dict[str, Any]]:
    d = json.loads(path.read_bytes())["d"]
    return d["results"] if isinstance(d, dict) else d


def write_ndjson(rows: list[dict[str, Any]], path: Path, fields: list[str]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({k: r.get(k) for k in fields}) + "\n")


def wcf_date_sql(col: str) -> str:
    return (
        f"CAST(epoch_ms(CAST(regexp_extract({col}, '/Date\\((-?\\d+)\\)/', 1) "
        f"AS BIGINT)) AS DATE)"
    )


def build(cfg: BuildConfig) -> None:
    cfg.tmp_dir.mkdir(parents=True, exist_ok=True)
    pages = sorted(cfg.pages_dir.glob("page_*.json"))
    rows: list[dict[str, Any]] = []
    for p in pages:
        rows += odata_rows(p)
    LOG.info("Read %d incident rows from %d pages", len(rows), len(pages))
    nd = cfg.tmp_dir / "safer_incidents.ndjson"
    write_ndjson(rows, nd, [f for f, _ in FIELDS])

    con = duckdb.connect()
    cols = {f: "VARCHAR" for f, _ in FIELDS}
    for f in ["IncidentReportId", "ProductCategoryId", "SeverityTypeId", "LocaleId",
              "VictimGenderId", "VictimAgeInMonths", "SourceTypeId"]:
        cols[f] = "BIGINT"
    col_spec = ", ".join(f"'{k}': '{v}'" for k, v in cols.items())
    con.execute(
        f"CREATE TABLE raw AS SELECT * FROM read_json('{nd}', "
        f"format='newline_delimited', columns={{{col_spec}}})"
    )

    def id_col(raw: str, out: str, typ: str) -> str:
        if out in cfg.minus_one_to_null:
            return f"CAST(NULLIF({raw}, -1) AS {typ}) AS {out}"
        return f"CAST({raw} AS {typ}) AS {out}"

    band = ["CASE WHEN victim_age_years IS NULL THEN 'unknown'"]
    for lo, hi, lab in cfg.age_bands:
        cond = f"victim_age_years >= {lo}"
        if hi is not None:
            cond += f" AND victim_age_years <= {hi}"
        band.append(f"WHEN {cond} THEN '{lab}'")
    band_sql = " ".join(band) + " END"

    con.execute(
        f"""
        CREATE TABLE safer_incidents AS
        WITH t AS (
          SELECT
            CAST(IncidentReportId AS INTEGER)              AS incident_report_id,
            IncidentReportNumber                           AS incident_report_number,
            {wcf_date_sql('IncidentReportDate')}           AS report_date,
            {wcf_date_sql('IncidentReportPublicationDate')} AS publication_date,
            {wcf_date_sql('IncidentDate')}                 AS incident_date,
            CAST(ProductCategoryId AS INTEGER)             AS sp_category_id,
            NULLIF(trim(IncidentProductDescription), '')   AS product_description,
            NULLIF(trim(ProductBrandName), '')             AS brand,
            NULLIF(trim(ProductModelName), '')             AS model,
            NULLIF(trim(ProductManufacturerName), '')      AS manufacturer_name,
            NULLIF(trim(ProductRetailCompanyName), '')     AS retailer_name,
            NULLIF(trim(IncidentDescription), '')          AS description,
            {id_col('SeverityTypeId', 'severity_type_id', 'SMALLINT')},
            {id_col('LocaleId', 'locale_id', 'SMALLINT')},
            {id_col('VictimGenderId', 'victim_sex_id', 'SMALLINT')},
            CAST(NULLIF(VictimAgeInMonths, 0) AS INTEGER)  AS victim_age_months,
            {id_col('SourceTypeId', 'source_type_id', 'SMALLINT')}
          FROM raw
        ), u AS (
          SELECT *, CAST(victim_age_months // 12 AS SMALLINT) AS victim_age_years
          FROM t
        )
        SELECT incident_report_id, incident_report_number, report_date,
               publication_date, incident_date, sp_category_id, product_description,
               brand, model, manufacturer_name, retailer_name, description,
               severity_type_id, locale_id, victim_sex_id, victim_age_months,
               victim_age_years, {band_sql} AS victim_age_group, source_type_id
        FROM u ORDER BY incident_report_id
        """
    )
    n, n_ids, n_bad_dates = con.sql(
        """SELECT count(*), count(DISTINCT incident_report_id),
                  count(*) FILTER (WHERE report_date IS NULL) FROM safer_incidents"""
    ).fetchall()[0]
    if n != n_ids or n < cfg.expected_rows_min or n_bad_dates:
        raise SystemExit(
            f"safer_incidents check failed: rows={n}, ids={n_ids}, "
            f"min={cfg.expected_rows_min}, null report dates={n_bad_dates}"
        )
    LOG.info("OK: %d rows, unique ids, all report dates parsed", n)

    # ---- codebook from lookup sets
    con.execute(
        """CREATE TABLE safer_codebook (lookup VARCHAR, id INTEGER,
           public_name VARCHAR, description VARCHAR)"""
    )
    for name, (id_f, pub_f, desc_f) in cfg.lookups.items():
        recs: list[dict[str, Any]] = []
        for p in sorted(cfg.lookups_dir.glob(f"{name}_p*.json")):
            recs += odata_rows(p)
        if not recs:
            raise SystemExit(f"Lookup {name} has no saved rows")
        con.executemany(
            "INSERT INTO safer_codebook VALUES (?, ?, ?, ?)",
            [(name, r[id_f], r[pub_f], r.get(desc_f)) for r in recs],
        )
        LOG.info("Lookup %s: %d rows", name, len(recs))
    dupes = con.sql(
        "SELECT lookup, id FROM safer_codebook GROUP BY ALL HAVING count(*) > 1"
    ).fetchall()
    if dupes:
        raise SystemExit(f"Duplicate codebook ids: {dupes[:10]}")
    for col, lk in [("severity_type_id", "SeverityTypes"), ("locale_id", "Locales"),
                    ("victim_sex_id", "Sexes"), ("source_type_id", "SourceTypes"),
                    ("sp_category_id", "ProductCategories")]:
        missing = con.sql(
            f"""SELECT DISTINCT {col} FROM safer_incidents WHERE {col} IS NOT NULL
                AND {col} NOT IN (SELECT id FROM safer_codebook
                                  WHERE lookup = '{lk}')"""
        ).fetchall()
        if missing:
            raise SystemExit(f"{col}: ids not in {lk}: {missing[:10]}")
        LOG.info("OK: all %s ids found in %s", col, lk)

    for table in ["safer_incidents", "safer_codebook"]:
        out = cfg.out_dir / f"{table}.parquet"
        con.execute(f"COPY {table} TO '{out}' (FORMAT parquet, COMPRESSION zstd)")
        cnt = con.sql(f"SELECT count(*) FROM {table}").fetchall()[0][0]
        LOG.info("Wrote %s: %d rows, %.1f MB", out.name, cnt, out.stat().st_size / 1e6)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", type=Path, default=REPO_ROOT / "build/config.yaml")
    build(BuildConfig.from_yaml(ap.parse_args().config))


if __name__ == "__main__":
    main()
