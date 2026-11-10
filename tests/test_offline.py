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
from detective.grounding import normalize_visits, ungrounded_numbers  # noqa: E402
from detective.present import build_caveats, build_chart, build_table  # noqa: E402
from detective.recall_match import plausible_for_product, product_nouns  # noqa: E402
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


def test_double_count_guardrail() -> None:
    assert guardrail_message(
        "SELECT age_group, SUM(weight) FROM v_case_products WHERE year = 2025 "
        "GROUP BY age_group")
    assert guardrail_message(
        "SELECT SUM(CASE WHEN product_code IN (1807, 1817) THEN weight END) "
        "FROM v_case_products")
    assert guardrail_message(
        "SELECT SUM(weight) FROM v_case_products WHERE product_code = 1233") is None
    assert guardrail_message(
        "SELECT product_code, SUM(weight) FROM v_case_products GROUP BY product_code"
    ) is None


def test_literal_select_rejected() -> None:
    assert guardrail_message(
        "SELECT '0-4' AS age_group, 3701 AS n_cases UNION ALL SELECT '65+', 8865")
    assert guardrail_message("SELECT 1") is not None
    assert guardrail_message("SELECT * FROM v_top_products LIMIT 1") is None


def test_visits_wording() -> None:
    assert normalize_visits("36% of all ER visits; 5 emergency room visits") == (
        "36% of all injury-related ER visits; 5 injury-related ER visits")
    done = "1 injury-related ER visits"
    assert normalize_visits(done) == done


def test_grounding() -> None:
    calls = [{"result": '{"rows": [[57638, 1469070.0, 36.56]]}'}]
    ok = "1,469,070 visits, about 1.47 million, 36.6%"
    assert ungrounded_numbers(ok, calls) == []
    assert ungrounded_numbers("3,904 cases", calls) == ["3,904"]


def test_guardrail_allows() -> None:
    ok = [
        "SELECT COUNT(*) AS n_cases, SUM(weight) AS est FROM neiss_cases "
        "WHERE year = 2025",
        "SELECT * FROM v_top_products WHERE year = 2025 AND rank <= 10",
        "SELECT count(*) FROM recalls WHERE search_text LIKE '%crib%'",
        "SELECT count(*) FROM v_safer_incidents_neiss WHERE neiss_code = 1842",
        "SELECT count(*) AS n FROM neiss_codebook",
        "SELECT age_raw, count(*) n, sum(weight) w FROM neiss_cases GROUP BY 1",
        "SELECT 'count(*) neiss_cases' AS s FROM recalls LIMIT 1",
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
    assert r["recalls"] and all("chair" in x["why"] for x in r["recalls"])
    r = call("search_recalls", {"keywords": "floor", "product_code": 1807})
    assert not r["recalls"] and r["message"].startswith("no reviewed recall link found")


def test_recall_relevance() -> None:
    floors = product_nouns("Floors or flooring materials")
    assert plausible_for_product("X Recalls Floor Lamps Due to Fire", ["Floor Lamp"],
                                 floors) is None
    assert plausible_for_product("X Recalls Laminate Flooring Due to Fire", [],
                                 floors) == "flooring"
    stairs = product_nouns("Stairs or steps (excluding pull-down and folding stairs)")
    assert plausible_for_product(
        "Viewrail Recalls Electronic LED Modules used with Floating Stairs and Rails "
        "Due to Fire Hazard", ["Electronic LED Modules"], stairs) is None
    beds = product_nouns("Beds or bedframes, other or not specified")
    assert plausible_for_product("Y Recalls Raised Garden Bed Due to Fire", [],
                                 beds) is None


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
    # Wide by year with no est_* names: every measure gets a line, n_cases does not.
    wide_years = {"columns": ["year", "bicycles", "e_bicycles", "n_cases"],
                  "rows": [[2019, 417673.4, 0.0, 9], [2020, 426109.4, 0.0, 8]],
                  "truncated": False, "sql": ""}
    chart = build_chart(wide_years, CFG)
    assert chart["type"] == "line" and chart["series"] == "column"
    assert {d["series"] for d in chart["data"]} == {"bicycles", "e_bicycles"}
    cav = build_caveats(st, CFG)
    assert any("not rates" in c for c in cav), "years comparison needs the rates note"


def test_age_comparison() -> None:
    st = RunState()
    st.record("run_sql", {"query": "SELECT age_group, n_cases, est_visits FROM "
                          "v_product_estimates WHERE age_group IN ('0-4', '65+')"},
              json.dumps({"columns": ["age_group", "n_cases", "est_visits"],
                          "rows": [["0-4", 3701, 81016.0], ["65+", 8865, 423006.0]],
                          "truncated": False}), "Product-Injury Detective")
    assert any("not rates" in c for c in build_caveats(st, CFG))
    chart = build_chart(build_table(st), CFG)
    assert chart["type"] == "bar" and len(chart["data"]) == 2
    wide = {"columns": ["product_title", "est_visits_0_4", "est_visits_65plus"],
            "rows": [["Stairs", 81016.0, 423006.0], ["Beds", 1.0, 2.0]],
            "truncated": False, "sql": ""}
    chart = build_chart(wide, CFG)
    assert chart["series"] == "column" and len(chart["data"]) == 4
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
