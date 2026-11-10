"""Read-only DuckDB access and the SQL checks behind run_sql and its guardrails.

The security boundary is the connection, not the prompt: the DB is opened read-only,
DuckDB may read only files under data/processed, and the configuration is locked so a
query can't SET its way out. On top of that, run_sql accepts exactly one SELECT.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any

import duckdb

from detective.config import REPO_ROOT, AgentConfig

NEISS_ROW_SOURCES = re.compile(
    r"\b(neiss_cases|neiss_case_products|v_case_products|neiss)\b", re.IGNORECASE
)
COUNT_CALL = re.compile(r"\bcount\s*\(", re.IGNORECASE)
WEIGHT_USE = re.compile(r"\b(weight|est_visits)\b", re.IGNORECASE)
AGE_RAW_COMPARE = re.compile(r"\bage_raw\s*(<|<=|>|>=|between)\s*", re.IGNORECASE)
GROUP_BY_P1 = re.compile(
    r"group\s+by\s+(?:[\w.]+\s*,\s*)*(?:\w+\.)?product_1\b", re.IGNORECASE
)
DATA_SOURCE = re.compile(
    r"\b(neiss_\w+|v_\w+|recalls|recall_products|safer_\w+|product_\w+|"
    r"data_dictionary|information_schema|duckdb_\w+)\b",
    re.IGNORECASE,
)
LONG_PRODUCTS = re.compile(r"\b(v_case_products|neiss_case_products)\b", re.I)
SUM_CALL = re.compile(r"\bsum\s*\(", re.IGNORECASE)
ONE_PRODUCT = re.compile(r"\bproduct_code\s*=\s*\d+", re.IGNORECASE)
GROUP_BY_PRODUCT = re.compile(
    r"group\s+by\b[^;]*?\bproduct_code\b", re.IGNORECASE | re.DOTALL
)
OTHER_SLOTS = re.compile(r"\bproduct_[23]\b|\bproduct_code\b", re.IGNORECASE)
ESTIMATE_VIEWS = re.compile(r"\b(v_product_estimates|v_top_products)\b", re.IGNORECASE)
SEVERAL_PRODUCTS = re.compile(r"\bproduct_code\s+in\s*\(\s*\d+\s*,", re.IGNORECASE)


def _strip_sql_comments_and_strings(query: str) -> str:
    """Remove comments and quoted strings so keyword checks only see SQL code."""
    query = re.sub(r"--[^\n]*", " ", query)
    query = re.sub(r"/\*.*?\*/", " ", query, flags=re.DOTALL)
    return re.sub(r"'(?:[^']|'')*'", "''", query)


def guardrail_message(query: str) -> str | None:
    """Why a query breaks a data rule, phrased so the model can fix it; None if OK."""
    code = _strip_sql_comments_and_strings(query)
    if not DATA_SOURCE.search(code):
        return (
            "Rejected: this query reads no table. Numbers must come from the data, so "
            "don't type values into SQL; query the table or view that holds them "
            "(e.g. v_product_estimates) and return all groups in that one query."
        )
    if (
        NEISS_ROW_SOURCES.search(code)
        and COUNT_CALL.search(code)
        and not WEIGHT_USE.search(code)
    ):
        return (
            "Rejected: this query counts NEISS rows without the sample weight. "
            "NEISS is a probability sample, so COUNT(*) is only the number of "
            "sample cases, not a national number. Add SUM(weight) AS est_visits "
            "(keep COUNT(*) AS n_cases next to it), or use the weighted views "
            "v_product_estimates / v_top_products (column est_visits)."
        )
    if AGE_RAW_COMPARE.search(code):
        return (
            "Rejected: age_raw is the raw NEISS code (201-223 = 1-23 months, 0 = not "
            "recorded), so range filters on it are wrong. Filter on age_years (e.g. "
            "age_years < 5 for under 5) or age_group instead."
        )
    if (
        LONG_PRODUCTS.search(code)
        and SUM_CALL.search(code)
        and not ONE_PRODUCT.search(code)
        and not GROUP_BY_PRODUCT.search(code)
    ):
        return (
            "Rejected: v_case_products / neiss_case_products have one row per "
            "(case, product), so SUM(weight) over several products counts a case "
            "once per product. For totals across products use neiss_cases; for a "
            "set of codes use neiss_cases WHERE product_1 IN (...) OR product_2 IN "
            "(...) OR product_3 IN (...); for one product filter product_code = N or "
            "GROUP BY product_code."
        )
    if (
        ESTIMATE_VIEWS.search(code)
        and SUM_CALL.search(code)
        and SEVERAL_PRODUCTS.search(code)
        and not GROUP_BY_PRODUCT.search(code)
    ):
        return (
            "Rejected: v_product_estimates / v_top_products have one row per product, "
            "so summing several products counts a case that lists two of them twice, "
            "and n_cases / low_sample stop meaning anything. To combine codes, query "
            "neiss_cases WHERE product_1 IN (...) OR product_2 IN (...) OR product_3 "
            "IN (...), with COUNT(*) AS n_cases and SUM(weight) AS est_visits."
        )
    if GROUP_BY_P1.search(code) and not OTHER_SLOTS.search(code):
        return (
            "Rejected: grouping by product_1 only counts the first product slot, but "
            "product order does not matter in NEISS. Count products in any slot: use "
            "v_product_estimates / v_top_products, or v_case_products grouped by "
            "product_code."
        )
    return None


def select_only_error(con: duckdb.DuckDBPyConnection, query: str) -> str | None:
    """Exactly one SELECT statement, or an error message."""
    try:
        statements = con.extract_statements(query)
    except duckdb.Error as e:
        return f"Could not parse the SQL: {e}"
    if len(statements) != 1:
        return f"Send exactly one SQL statement (got {len(statements)})."
    if statements[0].type != duckdb.StatementType.SELECT:
        return f"Only SELECT queries are allowed (got {statements[0].type.name})."
    return None


class Database:
    """A locked-down, read-only connection; each query runs on its own cursor."""

    def __init__(self, cfg: AgentConfig, plain: bool = False) -> None:
        self.cfg = cfg
        # The NEISS views read Parquet by repo-relative paths, and DuckDB checks those
        # paths against allowed_directories before resolving them via
        # file_search_path, so they only pass when the process runs in the repo root.
        if Path.cwd() != REPO_ROOT:
            os.chdir(REPO_ROOT)
        if not cfg.db_path.exists():
            raise SystemExit(
                f"No database at {cfg.db_path}. Run the build (docs/SCHEMA.md)."
            )
        if plain:
            # Baseline: raw NEISS column names over the same data, plus recalls.
            con = duckdb.connect()
            con.execute(f"SET file_search_path = '{REPO_ROOT}'")
            con.execute(f"ATTACH '{cfg.db_path}' AS src (READ_ONLY)")
            cols = ", ".join(
                f"{expr} AS {name}" for name, expr in cfg.plain_neiss_columns.items()
            )
            con.execute(f"CREATE VIEW neiss AS SELECT {cols} FROM src.neiss_cases")
            con.execute("CREATE VIEW recalls AS SELECT * FROM src.recalls")
            # Code -> title list, as published in the coding manual (no flags/rules).
            con.execute(
                "CREATE VIEW product_codes AS SELECT product_code, product_title "
                "FROM src.product_codes"
            )
        else:
            con = duckdb.connect(str(cfg.db_path), read_only=True)
            con.execute(f"SET file_search_path = '{REPO_ROOT}'")
        allowed = str(cfg.allowed_dir).rstrip("/") + "/"
        con.execute(f"SET allowed_directories = ['{allowed}']")
        con.execute("SET enable_external_access = false")
        con.execute("SET autoinstall_known_extensions = false")
        con.execute("SET lock_configuration = true")
        self.con = con

    def query(self, query: str, max_rows: int | None = None) -> dict[str, Any]:
        """Run one SELECT with a row cap and a timeout. Errors come back as data."""
        max_rows = max_rows or self.cfg.max_rows
        cur = self.con.cursor()
        try:
            err = select_only_error(cur, query)
            if err:
                return {"error": err}
            timer = threading.Timer(self.cfg.sql_timeout_s, cur.interrupt)
            timer.start()
            try:
                cur.execute(query)
                rows = cur.fetchmany(max_rows + 1)
            except duckdb.InterruptException:
                return {
                    "error": f"Query took longer than {self.cfg.sql_timeout_s:.0f} s "
                    "and was stopped. Filter by year or use the aggregated views."
                }
            except duckdb.Error as e:
                return {"error": str(e).splitlines()[0][:500]}
            finally:
                timer.cancel()
            columns = [c[0] for c in cur.description or []]
            return {
                "columns": columns,
                "rows": [list(r) for r in rows[:max_rows]],
                "truncated": len(rows) > max_rows,
            }
        finally:
            cur.close()

    def rows(self, query: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        """Internal parameterized read for tools (not exposed to the model)."""
        cur = self.con.cursor()
        try:
            cur.execute(query, params or [])
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        finally:
            cur.close()


def to_json(obj: Any) -> str:
    return json.dumps(obj, default=str)


def load_context(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(
            f"No agent context at {path}. Run build/build_agent_context.py."
        )
    return json.loads(path.read_text())
