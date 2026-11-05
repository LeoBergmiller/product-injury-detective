"""Build the NEISS Parquet tables defined in docs/SCHEMA.md.

Outputs (data/processed/):
  neiss_cases.parquet, neiss_case_products.parquet, neiss_codebook.parquet,
  product_code_stability.parquet

Steps:
1. Repair each yearly TSV into tmp/: a few narratives contain an unquoted CR LF, which
   splits one record across two physical lines. Lines are joined (CR LF -> one space)
   until the quote-aware field count reaches 25. Every repair is logged.
2. Load all years with DuckDB (all VARCHAR, strict), validate, and type per SCHEMA.md
   (age rule, 0 -> NULL for empty product slots, narrative era).
3. Long-form product slots (any-slot counting; deduplicated per case).
4. Codebook from the NEISS data dictionary (sheet "2019 and beyond").
5. Product-code presence by year (empirical stability; comparability PDFs pending).

Usage: uv run python build/build_neiss.py [--config build/config.yaml]
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import yaml

LOG = logging.getLogger("build_neiss")
REPO_ROOT = Path(__file__).resolve().parent.parent
XLSX_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
CODE_RE = re.compile(r"^\s*(\d+)\s*[-=]\s*(.+?)\s*$")
STRAY_NL_RE = re.compile(r"[\r\n]")
M_TAG = f"{{{XLSX_NS['m']}}}t"


@dataclass(frozen=True)
class NeissConfig:
    raw_dir: Path
    file_pattern: str
    years: list[int]
    tmp_dir: Path
    out_dir: Path
    product_codes_parquet: Path
    dictionary_xlsx: Path
    dictionary_sheet: str
    narrative_era_cutoff: str
    expected_columns: list[str]
    age_bands: list[tuple[int, int | None, str]]
    codebook_columns: dict[str, list[str]]
    unknown_codes: dict[str, list[int]]
    untitled_codes_allowed: list[int]

    @classmethod
    def from_yaml(cls, path: Path) -> NeissConfig:
        raw = yaml.safe_load(path.read_text())["neiss"]
        return cls(
            raw_dir=REPO_ROOT / raw["raw_dir"],
            file_pattern=raw["file_pattern"],
            years=[int(y) for y in raw["years"]],
            tmp_dir=REPO_ROOT / raw["tmp_dir"],
            out_dir=REPO_ROOT / raw["out_dir"],
            product_codes_parquet=REPO_ROOT / raw["product_codes_parquet"],
            dictionary_xlsx=REPO_ROOT / raw["dictionary_xlsx"],
            dictionary_sheet=raw["dictionary_sheet"],
            narrative_era_cutoff=raw["narrative_era_cutoff"],
            expected_columns=list(raw["expected_columns"]),
            age_bands=[(int(lo), None if hi is None else int(hi), lab)
                       for lo, hi, lab in raw["age_bands"]],
            codebook_columns={k: list(v) for k, v in raw["codebook_columns"].items()},
            unknown_codes={k: [int(c) for c in v]
                           for k, v in raw["unknown_codes"].items()},
            untitled_codes_allowed=[int(c) for c in raw["untitled_codes_allowed"]],
        )


# ---------------------------------------------------------------- 1. repair
def n_fields(text: str) -> int:
    """Quote-aware tab-separated field count of one logical record."""
    return len(next(csv.reader([text], delimiter="\t", quotechar='"')))


def repair_file(src: Path, dst: Path, n_cols: int) -> list[dict[str, Any]]:
    """Copy src to dst, joining records split by an unquoted CR LF. Return repairs."""
    repairs: list[dict[str, Any]] = []
    with src.open("r", encoding="ascii", newline="") as fin, dst.open(
        "w", encoding="ascii", newline=""
    ) as fout:
        lines = fin.read().split("\r\n")
        if lines and lines[-1] == "":
            lines.pop()
        fout.write(lines[0] + "\r\n")  # header
        for k in range(1, len(lines)):
            if "\n" in lines[k] or "\r" in lines[k]:
                repairs.append(
                    {
                        "file": src.name,
                        "type": "stray_cr_or_lf_in_field",
                        "physical_record": k + 1,
                        "case_id": lines[k].split("\t", 1)[0],
                    }
                )
                lines[k] = STRAY_NL_RE.sub(" ", lines[k])
        i = 1
        while i < len(lines):
            rec, start = lines[i], i
            while n_fields(rec) < n_cols and i + 1 < len(lines):
                i += 1
                rec = rec + " " + lines[i]
            nf = n_fields(rec)
            if nf != n_cols:
                raise SystemExit(
                    f"{src.name}: line {start + 1} has {nf} fields after repair"
                )
            if i > start:
                repairs.append(
                    {
                        "file": src.name,
                        "type": "record_split_by_crlf",
                        "physical_lines": [start + 1, i + 1],
                        "case_id": rec.split("\t", 1)[0],
                    }
                )
            fout.write(rec + "\r\n")
            i += 1
    return repairs


# ---------------------------------------------------------------- 4. codebook
def read_xlsx_sheet(path: Path, sheet_name: str) -> list[dict[str, str]]:
    """Rows of one sheet as {column letter: text}, stdlib only."""
    z = zipfile.ZipFile(path)
    shared: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        sst = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in sst.findall("m:si", XLSX_NS):
            shared.append("".join(t.text or "" for t in si.iter(M_TAG)))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    names = [s.get("name") for s in wb.find("m:sheets", XLSX_NS)]
    idx = names.index(sheet_name) + 1
    rows: list[dict[str, str]] = []
    sheet = ET.fromstring(z.read(f"xl/worksheets/sheet{idx}.xml"))
    for row in sheet.iter(f"{{{XLSX_NS['m']}}}row"):
        cells: dict[str, str] = {}
        for c in row.findall("m:c", XLSX_NS):
            v = c.find("m:v", XLSX_NS)
            txt = (
                v.text
                if v is not None
                else "".join(t.text or "" for t in c.iter(M_TAG))
            )
            if c.get("t") == "s" and txt is not None:
                txt = shared[int(txt)]
            cells[re.sub(r"\d+", "", c.get("r", ""))] = (txt or "").strip()
        rows.append(cells)
    return rows


def build_codebook(cfg: NeissConfig) -> list[tuple[str, int, str, bool]]:
    rows = read_xlsx_sheet(cfg.dictionary_xlsx, cfg.dictionary_sheet)
    current: str | None = None
    out: list[tuple[str, int, str, bool]] = []
    skipped: list[str] = []
    for r in rows[1:]:  # skip header row
        if r.get("A"):
            current = r["A"]
            continue
        text = r.get("C", "")
        if not text or current not in cfg.codebook_columns:
            continue
        m = CODE_RE.match(text)  # range rows like "2 to 120 - ..." don't match
        if not m:
            skipped.append(f"{current}: {text}")
            continue
        code, label = int(m.group(1)), m.group(2)
        for col in cfg.codebook_columns[current]:
            unknown = code in cfg.unknown_codes.get(col, [])
            out.append((col, code, label, unknown))
    LOG.info("Codebook: %d entries; skipped range rows: %s", len(out), skipped)
    return out


# ---------------------------------------------------------------- main build
def age_group_sql(cfg: NeissConfig) -> str:
    parts = ["CASE WHEN age_years IS NULL THEN 'unknown'"]
    for lo, hi, lab in cfg.age_bands:
        cond = f"age_years >= {lo}"
        if hi is not None:
            cond += f" AND age_years <= {hi}"
        parts.append(f"WHEN {cond} THEN '{lab}'")
    return " ".join(parts) + " END"


def check(con: duckdb.DuckDBPyConnection, name: str, sql: str) -> None:
    """Fail the build if `sql` returns any row."""
    bad = con.sql(sql).fetchall()
    if bad:
        raise SystemExit(f"Validation failed: {name}: {bad[:10]}")
    LOG.info("OK: %s", name)


def build(cfg: NeissConfig) -> None:
    cfg.tmp_dir.mkdir(parents=True, exist_ok=True)
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    clean: list[Path] = []
    all_repairs: list[dict[str, Any]] = []
    for year in cfg.years:
        src = cfg.raw_dir / cfg.file_pattern.format(year=year)
        if not src.exists():
            raise SystemExit(f"Missing NEISS file: {src}")
        header = src.open("r", encoding="ascii", newline="").readline()
        cols = [h.strip().strip('"') for h in header.rstrip("\r\n").split("\t")]
        if cols != cfg.expected_columns:
            raise SystemExit(f"{src.name}: header differs from expected: {cols}")
        dst = cfg.tmp_dir / f"{src.stem}.clean.tsv"
        reps = repair_file(src, dst, len(cfg.expected_columns))
        LOG.info("%s: header OK; %d split records repaired", src.name, len(reps))
        all_repairs += reps
        clean.append(dst)
    (cfg.tmp_dir / "neiss_repairs.json").write_text(json.dumps(all_repairs, indent=1))

    con = duckdb.connect()
    files = ", ".join(f"'{p}'" for p in clean)
    con.execute(
        f"""CREATE TABLE raw AS SELECT * FROM read_csv([{files}], delim='\t',
            header=true, all_varchar=true, quote='"', escape='"', filename=true,
            strict_mode=true)"""
    )
    n_raw = con.sql("SELECT count(*) FROM raw").fetchall()[0][0]
    LOG.info("Loaded %d raw rows from %d files", n_raw, len(clean))

    # ---- validation on raw values
    check(con, "case ids unique",
          "SELECT CPSC_Case_Number FROM raw GROUP BY 1 HAVING count(*) > 1")
    check(con, "dates parse",
          "SELECT Treatment_Date FROM raw "
          "WHERE try_strptime(Treatment_Date, '%m/%d/%Y') IS NULL")
    check(con, "date year matches file year",
          """SELECT filename, Treatment_Date FROM raw
             WHERE year(strptime(Treatment_Date, '%m/%d/%Y'))::VARCHAR
                   <> regexp_extract(filename, 'neiss(\\d{4})', 1)""")
    check(con, "age in {0, 2-120, 201-223}",
          """SELECT Age, count(*) FROM raw WHERE NOT (try_cast(Age AS INT) = 0
             OR try_cast(Age AS INT) BETWEEN 2 AND 120
             OR try_cast(Age AS INT) BETWEEN 201 AND 223) GROUP BY 1""")
    check(con, "weight numeric and > 0",
          "SELECT Weight FROM raw "
          "WHERE NOT coalesce(try_cast(Weight AS DOUBLE) > 0, false)")
    check(con, "product_1 non-zero integer",
          "SELECT Product_1 FROM raw WHERE coalesce(try_cast(Product_1 AS INT), 0) = 0")
    for c in ["Sex", "Race", "Hispanic", "Body_Part", "Diagnosis", "Disposition",
              "Location", "Fire_Involvement", "Product_2", "Product_3", "Alcohol",
              "Drug", "PSU"]:
        check(con, f"{c} integer",
              f"SELECT {c} FROM raw WHERE try_cast({c} AS INT) IS NULL LIMIT 5")

    # ---- typed neiss_cases (column order = docs/SCHEMA.md)
    con.execute(
        f"""
        CREATE TABLE neiss_cases AS
        WITH t AS (
          SELECT *,
            CAST(Age AS SMALLINT) AS a,
            CAST(strptime(Treatment_Date, '%m/%d/%Y') AS DATE) AS td
          FROM raw
        ), u AS (
          SELECT *,
            CASE WHEN a BETWEEN 201 AND 223 THEN a - 200 END AS age_months,
            CASE WHEN a = 0 THEN NULL
                 WHEN a BETWEEN 201 AND 223 THEN (a - 200) // 12
                 ELSE a END AS age_years
          FROM t
        )
        SELECT
          CAST(CPSC_Case_Number AS BIGINT)            AS case_id,
          td                                          AS treatment_date,
          CAST(year(td) AS SMALLINT)                  AS year,
          a                                           AS age_raw,
          CAST(age_months AS SMALLINT)                AS age_months,
          CAST(age_years AS SMALLINT)                 AS age_years,
          {age_group_sql(cfg)}                        AS age_group,
          CAST(Sex AS SMALLINT)                       AS sex,
          CAST(Race AS SMALLINT)                      AS race,
          NULLIF(trim(Other_Race), '')                AS race_other,
          CAST(Hispanic AS SMALLINT)                  AS hispanic,
          CAST(Body_Part AS SMALLINT)                 AS body_part,
          CAST(NULLIF(trim(Body_Part_2), '') AS SMALLINT) AS body_part_2,
          CAST(Diagnosis AS SMALLINT)                 AS diagnosis,
          NULLIF(trim(Other_Diagnosis), '')           AS diagnosis_other,
          CAST(NULLIF(trim(Diagnosis_2), '') AS SMALLINT) AS diagnosis_2,
          NULLIF(trim(Other_Diagnosis_2), '')         AS diagnosis_2_other,
          CAST(Disposition AS SMALLINT)               AS disposition,
          CAST(Location AS SMALLINT)                  AS location,
          CAST(Fire_Involvement AS SMALLINT)          AS fire_involvement,
          CAST(Product_1 AS INTEGER)                  AS product_1,
          NULLIF(CAST(Product_2 AS INTEGER), 0)       AS product_2,
          NULLIF(CAST(Product_3 AS INTEGER), 0)       AS product_3,
          CAST(Alcohol AS SMALLINT)                   AS alcohol,
          CAST(Drug AS SMALLINT)                      AS drug,
          Narrative_1                                 AS narrative,
          CASE WHEN td >= DATE '{cfg.narrative_era_cutoff}' THEN 'from_2019_400'
               ELSE 'pre_2019_142' END                AS narrative_era,
          Stratum                                     AS stratum,
          CAST(PSU AS SMALLINT)                       AS psu,
          CAST(Weight AS DOUBLE)                      AS weight,
          replace(regexp_extract(filename, '[^/]+$'),
                  '.clean.tsv', '.tsv')               AS source_file
        FROM u
        ORDER BY treatment_date, case_id
        """
    )
    check(con, "row count preserved",
          f"SELECT 1 WHERE (SELECT count(*) FROM neiss_cases) <> {n_raw}")
    check(con, "age group never NULL",
          "SELECT case_id FROM neiss_cases WHERE age_group IS NULL")

    # ---- neiss_case_products (any-slot, deduplicated per case)
    con.execute(
        """
        CREATE TABLE neiss_case_products AS
        SELECT case_id, CAST(min(slot) AS TINYINT) AS slot, product_code
        FROM (
          SELECT case_id, 1 AS slot, product_1 AS product_code FROM neiss_cases
          UNION ALL SELECT case_id, 2, product_2 FROM neiss_cases
            WHERE product_2 IS NOT NULL
          UNION ALL SELECT case_id, 3, product_3 FROM neiss_cases
            WHERE product_3 IS NOT NULL
        )
        GROUP BY case_id, product_code
        ORDER BY case_id, slot
        """
    )
    dup_slots = con.sql(
        """SELECT count(*) FROM neiss_cases WHERE product_2 = product_1
           OR product_3 = product_1 OR product_3 = product_2"""
    ).fetchall()[0][0]
    LOG.info("Cases where the same code fills two slots (deduplicated): %d", dup_slots)

    con.execute(
        f"CREATE TABLE product_codes AS SELECT * FROM "
        f"read_parquet('{cfg.product_codes_parquet}')"
    )
    allowed = ", ".join(str(c) for c in cfg.untitled_codes_allowed) or "NULL"
    check(con, "every product code has a title or is explicitly allowlisted",
          f"""SELECT DISTINCT p.product_code FROM neiss_case_products p
              ANTI JOIN product_codes c USING (product_code)
              WHERE p.product_code NOT IN ({allowed})""")
    untitled = con.sql(
        """SELECT p.product_code, count(*) FROM neiss_case_products p
           ANTI JOIN product_codes c USING (product_code) GROUP BY 1 ORDER BY 1"""
    ).fetchall()
    LOG.warning("Allowlisted untitled codes (code, case-product rows): %s", untitled)

    # ---- codebook
    con.execute(
        """CREATE TABLE neiss_codebook (variable VARCHAR, code SMALLINT,
           label VARCHAR, is_unknown BOOLEAN)"""
    )
    con.executemany(
        "INSERT INTO neiss_codebook VALUES (?, ?, ?, ?)", build_codebook(cfg)
    )
    for col in ["sex", "race", "hispanic", "body_part", "diagnosis", "disposition",
                "location", "fire_involvement", "alcohol", "drug"]:
        check(con, f"{col} codes all in codebook",
              f"""SELECT DISTINCT {col} FROM neiss_cases WHERE {col} IS NOT NULL
                  AND {col} NOT IN (SELECT code FROM neiss_codebook
                                    WHERE variable = '{col}')""")

    # ---- product code presence by year (empirical stability)
    years_sql = ", ".join(str(y) for y in cfg.years)
    con.execute(
        f"""
        CREATE TABLE product_code_stability AS
        WITH py AS (
          SELECT DISTINCT p.product_code, c.year FROM neiss_case_products p
          JOIN neiss_cases c USING (case_id)
        ), g AS (
          SELECT product_code, list(year ORDER BY year) AS yrs FROM py
          GROUP BY product_code
        )
        SELECT product_code,
               CAST(list_min(yrs) AS SMALLINT) AS first_year,
               CAST(list_max(yrs) AS SMALLINT) AS last_year,
               CAST(len(yrs) AS SMALLINT) AS n_years,
               yrs AS years_present,
               len(yrs) = {len(cfg.years)} AS present_all_years,
               NULL::BOOLEAN AS comparability_checked,
               CASE WHEN len(yrs) = {len(cfg.years)} THEN NULL
                    ELSE 'absent in ' || array_to_string(list_filter(
                      [{years_sql}], lambda y: NOT list_contains(yrs, y)), ',')
               END AS notes
        FROM g ORDER BY product_code
        """
    )

    # ---- write Parquet
    # neiss_cases is split into one file per year: with narratives the single file is
    # ~114 MB, over the 95 MB per-file limit (docs/SCHEMA.md changelog).
    cases_dir = cfg.out_dir / "neiss_cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    for old in cases_dir.glob("*.parquet"):
        old.unlink()
    legacy = cfg.out_dir / "neiss_cases.parquet"
    if legacy.exists():
        legacy.unlink()
    for year in cfg.years:
        out = cases_dir / f"neiss_cases_{year}.parquet"
        con.execute(
            f"COPY (SELECT * FROM neiss_cases WHERE year = {year} "
            f"ORDER BY treatment_date, case_id) TO '{out}' "
            "(FORMAT parquet, COMPRESSION zstd)"
        )
        n = con.sql(f"SELECT count(*) FROM neiss_cases WHERE year = {year}")
        LOG.info("Wrote neiss_cases/%s: %d rows, %.1f MB", out.name,
                 n.fetchall()[0][0], out.stat().st_size / 1e6)
    for table in ["neiss_case_products", "neiss_codebook", "product_code_stability"]:
        out = cfg.out_dir / f"{table}.parquet"
        con.execute(f"COPY {table} TO '{out}' (FORMAT parquet, COMPRESSION zstd)")
        n = con.sql(f"SELECT count(*) FROM {table}").fetchall()[0][0]
        LOG.info("Wrote %s: %d rows, %.1f MB", out.name, n, out.stat().st_size / 1e6)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", type=Path, default=REPO_ROOT / "build/config.yaml")
    build(NeissConfig.from_yaml(ap.parse_args().config))


if __name__ == "__main__":
    main()
