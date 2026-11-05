"""Assemble data/processed/injuries.duckdb from the Parquet outputs (docs/SCHEMA.md).

1. Parse the column tables in docs/SCHEMA.md (the single source of truth).
2. Validate every Parquet: exact column names and order, types, NOT NULL columns,
   grain uniqueness.
3. Build a "thin" DB: big NEISS tables are views over Parquet (repo-relative paths;
   open the DB with the working directory at the repo root); everything else is stored.
4. Create the six views and the data_dictionary table. Optional BM25 index on recalls
   only if the DuckDB `fts` extension is ALREADY installed (no auto-install/network).
5. Refuse any file in data/processed over max_file_mb (the DB is deleted if too big).
6. Sanity checks against known numbers.

Usage: uv run python build/build_db.py [--config build/config.yaml]
"""

from __future__ import annotations

import argparse
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import yaml

LOG = logging.getLogger("build_db")
REPO_ROOT = Path(__file__).resolve().parent.parent
PIPE_SPLIT = re.compile(r"(?<!\\)\|")


@dataclass(frozen=True)
class ColSpec:
    name: str
    type: str
    nullable: bool
    notes: str


@dataclass(frozen=True)
class DbConfig:
    processed_dir: Path
    schema_md: Path
    db_path: Path
    max_file_mb: float
    min_cases: int
    parquet_overrides: dict[str, str]
    materialize: list[str]
    grains: dict[str, list[str]]
    untitled_label: str
    sanity: dict[str, Any]

    @classmethod
    def from_yaml(cls, path: Path) -> DbConfig:
        raw = yaml.safe_load(path.read_text())["build_db"]
        return cls(
            processed_dir=REPO_ROOT / raw["processed_dir"],
            schema_md=REPO_ROOT / raw["schema_md"],
            db_path=REPO_ROOT / raw["db_path"],
            max_file_mb=float(raw["max_file_mb"]),
            min_cases=int(raw["min_cases"]),
            parquet_overrides=dict(raw["parquet_overrides"]),
            materialize=list(raw["materialize"] or []),
            grains={k: list(v) for k, v in raw["grains"].items()},
            untitled_label=raw["untitled_label"],
            sanity=dict(raw["sanity"]),
        )


# ------------------------------------------------------------------ 1. contract
def parse_schema(md: Path) -> dict[str, list[ColSpec]]:
    """Map '### table' headings to their '| column | type | null | notes |' tables."""
    tables: dict[str, list[ColSpec]] = {}
    current: str | None = None
    in_table = False
    for line in md.read_text().splitlines():
        h = re.match(r"^### (\w+)\s*$", line)
        if h:
            current, in_table = h.group(1), False
            continue
        if line.startswith("## "):
            current, in_table = None, False
            continue
        if current is None or not line.startswith("|"):
            in_table = False if not line.startswith("|") else in_table
            continue
        cells = [c.strip().replace("\\|", "|") for c in PIPE_SPLIT.split(line)[1:-1]]
        if [c.lower() for c in cells] == ["column", "type", "null", "notes"]:
            in_table = True
            tables[current] = []
            continue
        if not in_table or set(cells[0]) <= {"-"}:
            continue
        if cells[2] not in ("yes", "no"):
            raise SystemExit(f"SCHEMA.md {current}.{cells[0]}: null must be yes/no")
        tables[current].append(ColSpec(cells[0], cells[1].upper(), cells[2] == "yes",
                                       cells[3]))
    return {t: cols for t, cols in tables.items() if cols}


# ------------------------------------------------------------------ 2. validate
def parquet_source(cfg: DbConfig, table: str) -> str:
    """Repo-relative read_parquet() source for a contract table."""
    if table in cfg.parquet_overrides:
        return f"read_parquet('{cfg.parquet_overrides[table]}')"
    rel = cfg.processed_dir.relative_to(REPO_ROOT)
    return f"read_parquet('{rel}/{table}.parquet')"


def validate(con: duckdb.DuckDBPyConnection, cfg: DbConfig,
             schema: dict[str, list[ColSpec]]) -> None:
    for table, spec in schema.items():
        src = parquet_source(cfg, table)
        actual = [(r[0], r[1].upper()) for r in con.sql(f"DESCRIBE SELECT * FROM {src}")
                  .fetchall()]
        expected = [(c.name, c.type) for c in spec]
        if actual != expected:
            diff = [(e, a) for e, a in zip(expected, actual) if e != a]
            raise SystemExit(
                f"{table}: columns/types differ from SCHEMA.md.\n"
                f"  expected {expected}\n  actual   {actual}\n"
                f"  first diffs {diff[:5]}"
            )
        not_null = [c.name for c in spec if not c.nullable]
        if not_null:
            exprs = ", ".join(f"count(*) FILTER (WHERE {c} IS NULL)" for c in not_null)
            nulls = con.sql(f"SELECT {exprs} FROM {src}").fetchall()[0]
            bad = {c: n for c, n in zip(not_null, nulls) if n}
            if bad:
                raise SystemExit(f"{table}: NULLs in NOT NULL columns: {bad}")
        grain = cfg.grains.get(table)
        if grain:
            keys = ", ".join(grain)
            dup = con.sql(
                f"SELECT {keys}, count(*) FROM {src} GROUP BY ALL HAVING count(*) > 1 "
                "LIMIT 3"
            ).fetchall()
            if dup:
                raise SystemExit(f"{table}: grain ({keys}) not unique, e.g. {dup}")
        n = con.sql(f"SELECT count(*) FROM {src}").fetchall()[0][0]
        LOG.info("Valid: %-24s %9d rows, %d columns", table, n, len(spec))


# ------------------------------------------------------------------ 3-4. build
VIEWS_SQL = """
CREATE VIEW v_case_products AS
SELECT c.*, p.product_code, p.slot,
       coalesce(pc.product_title,
                replace('{untitled}', '{{code}}', CAST(p.product_code AS VARCHAR)))
         AS product_title,
       coalesce(pc.is_placeholder, false) AS is_placeholder,
       coalesce(pc.is_catch_all, false) AS is_catch_all,
       pc.product_code IS NULL AS title_unknown
FROM neiss_cases c
JOIN neiss_case_products p USING (case_id)
LEFT JOIN product_codes pc USING (product_code);

CREATE VIEW v_product_estimates AS
SELECT year,
       coalesce(age_group, 'all') AS age_group,
       product_code,
       any_value(product_title) AS product_title,
       count(*) AS n_cases,
       sum(weight) AS est_visits,
       count(*) < {min_cases} AS low_sample,
       any_value(is_placeholder) AS is_placeholder,
       any_value(is_catch_all) AS is_catch_all,
       any_value(title_unknown) AS title_unknown
FROM v_case_products
GROUP BY GROUPING SETS ((year, age_group, product_code), (year, product_code));

CREATE VIEW v_top_products AS
SELECT *, rank() OVER (PARTITION BY year, age_group ORDER BY est_visits DESC) AS rank
FROM v_product_estimates
WHERE NOT is_placeholder;

CREATE VIEW v_recall_search AS
SELECT recall_id, recall_date, title, hazards_text, search_text FROM recalls;

CREATE VIEW v_recalls_for_product AS
SELECT x.neiss_code, pc.product_title, x.recall_id, r.recall_date,
       r.title AS recall_title, r.url, r.hazards_text, x.search_terms, x.match_method,
       x.confidence, x.audited, x.scope_groups, x.notes
FROM product_recall_xwalk x
JOIN recalls r USING (recall_id)
LEFT JOIN product_codes pc ON pc.product_code = x.neiss_code;

CREATE VIEW v_safer_incidents_neiss AS
SELECT i.*, x.neiss_code, pc.product_title AS neiss_product_title,
       x.sp_duplicate_name, cat.public_name AS sp_category_name,
       sx.public_name AS victim_sex_label, sev.public_name AS severity_label
FROM safer_incidents i
LEFT JOIN safer_category_xwalk x USING (sp_category_id)
LEFT JOIN product_codes pc ON pc.product_code = x.neiss_code
LEFT JOIN safer_codebook cat ON cat.lookup = 'ProductCategories'
     AND cat.id = i.sp_category_id
LEFT JOIN safer_codebook sx ON sx.lookup = 'Sexes' AND sx.id = i.victim_sex_id
LEFT JOIN safer_codebook sev ON sev.lookup = 'SeverityTypes'
     AND sev.id = i.severity_type_id;
"""
VIEW_NAMES = ["v_case_products", "v_product_estimates", "v_top_products",
              "v_recall_search", "v_recalls_for_product", "v_safer_incidents_neiss"]


def view_docs(md: Path) -> dict[str, str]:
    docs: dict[str, str] = {}
    for line in md.read_text().splitlines():
        m = re.match(r"^\| `(v_\w+)` \| (.+) \|\s*$", line)
        if m:
            docs[m.group(1)] = m.group(2)
    return docs


def assemble(cfg: DbConfig, schema: dict[str, list[ColSpec]]) -> Path:
    tmp = cfg.db_path.with_suffix(".duckdb.tmp")
    for p in (tmp, Path(str(tmp) + ".wal")):
        if p.exists():
            p.unlink()
    con = duckdb.connect(str(tmp))
    con.execute("SET autoinstall_known_extensions = false")
    for table in schema:
        src = parquet_source(cfg, table)
        kind = "TABLE" if table in cfg.materialize else "VIEW"
        con.execute(f"CREATE {kind} {table} AS SELECT * FROM {src}")
        LOG.info("Created %s %s", kind.lower(), table)
    con.execute(VIEWS_SQL.format(untitled=cfg.untitled_label.replace("'", "''"),
                                 min_cases=cfg.min_cases))

    # data dictionary
    con.execute(
        """CREATE TABLE data_dictionary (table_name VARCHAR, column_name VARCHAR,
           type VARCHAR, nullable BOOLEAN, notes VARCHAR)"""
    )
    rows = [(t, c.name, c.type, c.nullable, c.notes)
            for t, spec in schema.items() for c in spec]
    docs = view_docs(cfg.schema_md)
    rows += [(v, "*", "VIEW", None, docs.get(v, "")) for v in VIEW_NAMES]
    con.executemany("INSERT INTO data_dictionary VALUES (?, ?, ?, ?, ?)", rows)
    LOG.info("data_dictionary: %d rows", len(rows))

    # optional full-text index (only if fts is already installed locally)
    try:
        con.execute("LOAD fts")
        con.execute(
            "PRAGMA create_fts_index('recalls', 'recall_id', 'search_text', "
            "overwrite = 1)"
        )
        LOG.info("FTS: BM25 index created on recalls.search_text")
    except duckdb.Error as err:
        LOG.warning("FTS: not created (%s). v_recall_search works with ILIKE.",
                    str(err).splitlines()[0][:120])
    con.execute("CHECKPOINT")
    con.close()
    if cfg.db_path.exists():
        cfg.db_path.unlink()
    tmp.replace(cfg.db_path)
    return cfg.db_path


# ------------------------------------------------------------------ 5. sizes
def enforce_sizes(cfg: DbConfig) -> list[tuple[str, float]]:
    sizes: list[tuple[str, float]] = []
    too_big: list[str] = []
    for p in sorted(cfg.processed_dir.rglob("*")):
        if not p.is_file() or "tmp" in p.relative_to(cfg.processed_dir).parts:
            continue
        mb = p.stat().st_size / 1e6
        sizes.append((str(p.relative_to(REPO_ROOT)), round(mb, 1)))
        if mb > cfg.max_file_mb:
            too_big.append(f"{p.relative_to(REPO_ROOT)} ({mb:.1f} MB)")
    if too_big:
        if cfg.db_path.exists() and any(str(cfg.db_path.name) in t for t in too_big):
            cfg.db_path.unlink()
        raise SystemExit(f"Files over {cfg.max_file_mb} MB (refused): {too_big}")
    total = sum(s for _, s in sizes)
    LOG.info("All %d files <= %.0f MB; total %.1f MB",
             len(sizes), cfg.max_file_mb, total)
    return sizes


# ------------------------------------------------------------------ 6. sanity
def sanity(cfg: DbConfig) -> None:
    s = cfg.sanity
    con = duckdb.connect(str(cfg.db_path), read_only=True)

    def one(sql: str) -> Any:
        return con.sql(sql).fetchall()[0][0]

    checks: list[tuple[str, Any, Any]] = [
        ("2025 weight total", round(one(
            "SELECT sum(weight) FROM neiss_cases WHERE year = 2025")),
         s["weight_total_2025"]),
        ("2025 under-5 (age_years < 5), weighted", round(one(
            "SELECT sum(weight) FROM neiss_cases WHERE year = 2025 AND age_years < 5")),
         s["under5_2025_correct"]),
        ("2025 naive age_raw < 5, weighted", round(one(
            "SELECT sum(weight) FROM neiss_cases WHERE year = 2025 AND age_raw < 5")),
         s["under5_2025_naive"]),
        ("2025 age_group '0-4' (weighted) equals age_years < 5",
         round(one("SELECT sum(weight) FROM neiss_cases WHERE year = 2025 "
                   "AND age_group = '0-4'")), s["under5_2025_correct"]),
        ("years present", [r[0] for r in con.sql(
            "SELECT DISTINCT year FROM neiss_cases ORDER BY 1").fetchall()],
         s["years"]),
        ("safer_incidents rows >= expected", one(
            "SELECT count(*) FROM safer_incidents") >= s["safer_incidents_min"], True),
        ("recalls rows >= expected", one("SELECT count(*) FROM recalls")
         >= s["recalls_min"], True),
        ("2025 rank-1 product (all ages) is 1807 floors", one(
            "SELECT product_code FROM v_top_products WHERE year = 2025 "
            "AND age_group = 'all' AND rank = 1"), 1807),
        ("'all' row = sum of age-group rows (1807, 2025)", round(one(
            "SELECT sum(est_visits) FILTER (WHERE age_group = 'all') - "
            "sum(est_visits) FILTER (WHERE age_group <> 'all') "
            "FROM v_product_estimates WHERE year = 2025 AND product_code = 1807"), 6),
         0),
        ("any-slot visits >= total visits (2025)", one(
            "SELECT (SELECT sum(est_visits) FROM v_product_estimates WHERE year = 2025 "
            "AND age_group = 'all') >= (SELECT sum(weight) FROM neiss_cases "
            "WHERE year = 2025)"), True),
        ("no audited recall links yet", one(
            "SELECT count(*) FROM product_recall_xwalk WHERE audited"), 0),
    ]
    failed = []
    for name, got, want in checks:
        ok = got == want
        LOG.info("%s %s: got %s, expected %s",
                 "OK  " if ok else "FAIL", name, got, want)
        if not ok:
            failed.append(name)
    linked = one("SELECT round(100.0 * count(neiss_code) / count(*), 1) "
                 "FROM v_safer_incidents_neiss")
    LOG.info("Info: SaferProducts incidents linked to a NEISS code: %s%%", linked)
    con.close()
    if failed:
        raise SystemExit(f"Sanity checks failed: {failed}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", type=Path, default=REPO_ROOT / "build/config.yaml")
    cfg = DbConfig.from_yaml(ap.parse_args().config)
    os.chdir(REPO_ROOT)  # view paths are repo-relative
    schema = parse_schema(cfg.schema_md)
    LOG.info("SCHEMA.md tables: %s", list(schema))
    con = duckdb.connect()
    validate(con, cfg, schema)
    con.close()
    db = assemble(cfg, schema)
    LOG.info("Wrote %s (%.1f MB)", db.relative_to(REPO_ROOT), db.stat().st_size / 1e6)
    enforce_sizes(cfg)
    sanity(cfg)


if __name__ == "__main__":
    main()
