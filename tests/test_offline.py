"""Offline checks (no model calls): SQL safety, guardrails, tools, presentation.

Usage: uv run python tests/test_offline.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agents.tool_context import ToolContext  # noqa: E402

from detective.agent import Detective  # noqa: E402
from detective.config import AgentConfig  # noqa: E402
from detective.db import guardrail_message  # noqa: E402
from detective.present import build_caveats, build_chart, build_table  # noqa: E402
from detective.tools import RunState  # noqa: E402

LOG = logging.getLogger("test_offline")
CFG = AgentConfig.load()
DET = Detective.build(CFG, "detective")
TOOLS = {t.name: t for t in DET.agent.tools}


def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    ctx = ToolContext(context=RunState(), tool_name=name, tool_call_id="t",
                      tool_arguments=json.dumps(args))
    return json.loads(asyncio.run(TOOLS[name].on_invoke_tool(ctx, json.dumps(args))))


def test_guardrail_rejects() -> None:
    bad = [
        "SELECT COUNT(*) FROM neiss_cases WHERE year = 2025",
        "select count(*) from v_case_products where product_code = 1807",
        "SELECT COUNT(*) FROM neiss_cases WHERE age_raw < 5",
        "SELECT age_years, count(*) FROM neiss_cases WHERE weight > 0 AND age_raw <= 4"
        " GROUP BY 1",
        "SELECT product_1, SUM(weight) FROM neiss_cases GROUP BY product_1",
    ]
    for q in bad:
        assert guardrail_message(q), f"should reject: {q}"


def test_guardrail_allows() -> None:
    ok = [
        "SELECT COUNT(*) AS n_cases, SUM(weight) AS est FROM neiss_cases "
        "WHERE year = 2025",
        "SELECT * FROM v_top_products WHERE year = 2025 AND rank <= 10",
        "SELECT count(*) FROM recalls WHERE search_text LIKE '%crib%'",
        "SELECT count(*) FROM v_safer_incidents_neiss WHERE neiss_code = 1842",
        "SELECT count(*) AS n FROM neiss_codebook",
        "SELECT age_raw, count(*) n, sum(weight) w FROM neiss_cases GROUP BY 1",
        "SELECT 'count(*) neiss_cases' AS s",
    ]
    for q in ok:
        assert guardrail_message(q) is None, f"should allow: {q}"


def test_sql_safety() -> None:
    assert "error" in DET.db.query("DROP VIEW v_top_products")
    assert "error" in DET.db.query("SELECT 1; SELECT 2")
    assert "error" in DET.db.query("SELECT * FROM read_text('.env')")
    assert "error" in DET.db.query("SET enable_external_access = true")
    assert "error" in DET.db.query("COPY recalls TO 'x.csv'")
    res = DET.db.query("SELECT recall_id FROM recalls")
    assert len(res["rows"]) == CFG.max_rows and res["truncated"]


def test_known_numbers() -> None:
    res = DET.db.query(
        "SELECT round(sum(weight)) FROM neiss_cases WHERE year=2025 AND age_years < 5"
    )
    assert res["rows"][0][0] == 1469070


def test_tools() -> None:
    r = call("lookup_codes", {"kind": "product", "query": "stairs"})
    assert any(m["code"] == 1842 for m in r["matches"])
    assert "error" in call("lookup_codes", {"kind": "nope"})
    assert call("describe_data", {"table": "v_top_products"})["kind"] == "view"
    r = call("search_recalls", {"keywords": "bunk bed", "since_year": 2020})
    assert r["recalls"] and all(x["url"].startswith("https://www.cpsc.gov")
                                for x in r["recalls"])
    assert all(x["link_status"].startswith("possible match") for x in r["recalls"])
    r = call("search_recalls", {"keywords": "chair", "product_code": 4074})
    assert r["source"] == "product_recall_xwalk"


def test_presentation() -> None:
    st = RunState()
    st.record("run_sql", {"query": "SELECT ... FROM v_top_products"}, json.dumps({
        "columns": ["rank", "product_title", "n_cases", "est_visits", "low_sample"],
        "rows": [[1, "Floors", 100, 3e6, False], [2, "Stairs", 12, 1e6, True]],
        "truncated": False}), "Product-Injury Detective")
    chart = build_chart(build_table(st), CFG)
    assert chart and chart["type"] == "bar" and chart["y"] == "est_visits"
    assert any("fewer than" in c for c in build_caveats(st, CFG))
    st.record("run_sql", {"query": "SELECT year, est_visits FROM v_product_estimates"},
              json.dumps({"columns": ["year", "est_visits"],
                          "rows": [[2019, 1.0], [2020, 2.0]], "truncated": False}),
              "Product-Injury Detective")
    assert build_chart(build_table(st), CFG)["type"] == "line"
    assert st.tool_calls[0]["agent"] == "Product-Injury Detective"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                LOG.info("PASS %s", name)
            except AssertionError as e:
                failed += 1
                LOG.error("FAIL %s: %s", name, e)
    LOG.info("%s", "all offline tests passed" if not failed else f"{failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
