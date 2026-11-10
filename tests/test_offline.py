"""Offline checks (no model calls): SQL safety, guardrails, tools, grounding, answer
clean-up, presentation, and the app's endpoints and session cap (in a subprocess).

Usage: uv run python tests/test_offline.py   (or: uv run pytest -q tests/test_offline.py)
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
from agents.tool_guardrails import ToolInputGuardrailData  # noqa: E402

from detective.agent import Detective  # noqa: E402
from detective.config import AgentConfig  # noqa: E402
from detective.db import filtered_product_codes, guardrail_message  # noqa: E402
from detective.grounding import (  # noqa: E402
    ensure_cannot,
    normalize_visits,
    refusal_first,
    ungrounded_numbers,
)
from detective.present import (  # noqa: E402
    COUNTS_NOT_RATES,
    add_partial_code_note,
    build_caveats,
    build_chart,
    build_table,
    drop_stated,
    partial_code_note,
)
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


def test_filtered_product_codes() -> None:
    cases = {
        "SELECT * FROM v_product_estimates WHERE product_code = 1531": {1531},
        "SELECT * FROM v_case_products p WHERE p.product_code IN (1522, 1523)": {1522, 1523},
        "SELECT * FROM neiss_cases WHERE 1842 IN (product_1, product_2, product_3)": {1842},
        "SELECT * FROM neiss_cases WHERE product_1 IN (1807) OR product_2 IN (1807)": {1807},
        "SELECT * FROM v_recalls_for_product WHERE neiss_code=4076": {4076},
        "SELECT * FROM v_top_products WHERE product_code <> 5555 AND year = 2025": set(),
        # a subquery has no literal codes; years and ranks are not codes
        "SELECT * FROM v_case_products WHERE product_code IN (SELECT product_code "
        "FROM v_top_products WHERE year = 2025 AND rank <= 10)": set(),
        "SELECT * FROM v_top_products WHERE year = 2025 -- product_code = 1531": set(),
    }
    for q, want in cases.items():
        assert filtered_product_codes(q) == want, (q, filtered_product_codes(q))


def _code_guard(tool: str, args: dict[str, Any], state: RunState) -> str | None:
    """The product-code guardrail's rejection message for one call, or None."""
    guard = next(g for g in TOOLS[tool].tool_input_guardrails
                 if g.get_name() == "product_code_guardrail")
    ctx = ToolContext(context=state, tool_name=tool, tool_call_id="t",
                      tool_arguments=json.dumps(args))
    out = asyncio.run(guard.run(ToolInputGuardrailData(context=ctx, agent=DET.agent)))
    return None if out.behavior["type"] == "allow" else str(out.output_info or
                                                             out.behavior.get("message"))


def test_guessed_product_code_rejected() -> None:
    q = {"query": "SELECT * FROM v_product_estimates WHERE product_code = 1531"}
    state = RunState()
    msg = _code_guard("run_sql", q, state) or ""
    assert "1531" in msg and "bicycle-mounted" in msg and "lookup_codes" in msg, msg
    assert state.tool_calls and "rejected" in state.tool_calls[-1]["result"]
    assert _code_guard("search_recalls", {"keywords": "stroller", "product_code": 1531},
                       RunState())
    # After a lookup that returns the code, the same query passes.
    state.record("lookup_codes", {"kind": "product", "query": "stroller"},
                 json.dumps({"matches": [{"code": 1522, "title": "Baby strollers"}]}),
                 "a")
    ok = {"query": "SELECT * FROM v_product_estimates WHERE product_code = 1522"}
    assert _code_guard("run_sql", ok, state) is None
    assert _code_guard("search_recalls", {"keywords": "stroller", "product_code": 1522},
                       state) is None
    # Codes seen in an earlier query result count too (e.g. from v_top_products).
    state.record("run_sql", {"query": "SELECT ..."},
                 json.dumps({"columns": ["rank", "product_code"], "rows": [[1, 1807]]}),
                 "a")
    assert _code_guard("run_sql", {"query": "SELECT * FROM v_case_products "
                                   "WHERE product_code = 1807"}, state) is None
    # The 5555 placeholder may always be excluded; queries with no codes pass.
    assert _code_guard("run_sql", {"query": "SELECT * FROM neiss_cases WHERE "
                                   "5555 NOT IN (product_1, product_2, product_3)"},
                       RunState()) is None
    assert _code_guard("search_recalls", {"keywords": "stroller"}, RunState()) is None


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
    # Bare 3+ digit numbers are checked too; years and ranges are not.
    assert ungrounded_numbers("257 sample cases in 2019-2025", calls) == ["257"]
    assert normalize_visits(
        "an estimated 1,469,070 estimated ER visits") == (
        "an estimated 1,469,070 injury-related ER visits")
    assert normalize_visits("2019: 11,873 visits ($n = 487$); costs $5") == (
        "2019: 11,873 visits (n = 487); costs $5")



def test_share_denominator_rejected() -> None:
    # Seen live: a CASE on one product next to SUM(weight) over every product row.
    q = ("SELECT SUM(CASE WHEN product_code = 1807 THEN weight ELSE 0 END) AS floors, "
         "SUM(weight) AS total FROM v_case_products WHERE year = 2025 "
         "AND age_group = '65+'")
    assert "once per product" in (guardrail_message(q) or "")
    one = ("SELECT SUM(weight) FROM v_case_products WHERE year = 2025 "
           "AND product_code = 1807")
    assert guardrail_message(one) is None


def test_positional_group_by_allowed() -> None:
    # Seen live, wrongly rejected: GROUP BY 1, 2 where column 1 is product_code.
    ok = ("SELECT product_code, product_title, COUNT(*) AS n_cases, "
          "ROUND(SUM(weight)) AS est_visits FROM v_case_products WHERE year = 2025 "
          "AND product_code IN (1401, 1439) GROUP BY 1, 2 ORDER BY est_visits DESC")
    assert guardrail_message(ok) is None
    assert guardrail_message(ok.replace("GROUP BY 1, 2", "GROUP BY ALL")) is None
    # Still rejected: position 1 is a CASE that merges products into groups.
    merged = ("SELECT CASE WHEN p.product_code IN (5033, 5040) THEN 'bikes' END AS g, "
              "ROUND(SUM(p.weight)) AS est FROM v_case_products p WHERE p.year = 2025 "
              "AND p.product_code IN (5033, 5040, 1333) GROUP BY 1")
    assert guardrail_message(merged)


def test_million_in_tool_text() -> None:
    calls = [{"result": '{"rows": [[9147, "Boppy Recalls Over 3 Million Loungers"]]}'}]
    assert ungrounded_numbers("a recall of over 3 million loungers", calls) == []


def test_year_outside_data() -> None:
    q = "How many people were injured by lawn mowers in 2012?"
    out = ensure_cannot(q, "In 2025 there were 54,139 visits.", (2019, 2025))
    assert out.startswith("The data cannot answer this for 2012")
    said = "The data only covers 2019 to 2025. In 2025 there were 54,139 visits."
    assert ensure_cannot(q, said, (2019, 2025)) == said
    assert ensure_cannot("Lawn mowers in 2024?", "54,139 visits.", (2019, 2025)) == (
        "54,139 visits.")

def test_summed_estimates_rejected() -> None:
    q = ("SELECT age_group, SUM(n_cases), SUM(est_visits) FROM v_product_estimates "
         "WHERE year = 2025 AND product_code IN (1840, 1842) GROUP BY age_group")
    assert "one row per product" in (guardrail_message(q) or "")
    one = ("SELECT age_group, n_cases, est_visits FROM v_product_estimates "
           "WHERE year = 2025 AND product_code IN (1842) ")
    assert guardrail_message(one) is None
    per = q.replace("GROUP BY age_group", "GROUP BY age_group, product_code")
    assert guardrail_message(per) is None


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
    assert "error" in call("lookup_codes", {"kind": "product", "query": "??"})
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
    # A specific modifier must match too: bath seats and golf cars are not car seats.
    car_seats = product_nouns("Car seats (for infants or children)")
    assert car_seats == {"car seat"}
    assert plausible_for_product("X Recalls Baby Bath Seats Due to Drowning", [],
                                 car_seats) is None
    assert plausible_for_product("Yamaha Recalls Fleet Golf Cars Due to Crash", [],
                                 car_seats) is None
    assert plausible_for_product("Z Recalls Convertible Car Seats Due to Fall", [],
                                 car_seats) == "car seat"
    # A generic modifier is optional ("Baby strollers" are strollers), and "baby" alone
    # is no longer a product word, so baby bath seats are not stroller recalls.
    strollers = product_nouns("Baby strollers")
    assert plausible_for_product("AliExpress Recalls Convertible Strollers Due to Fall",
                                 [], strollers) == "stroller"
    assert plausible_for_product("Baby Bath Seats Recalled Due to Drowning",
                                 ["Baby Bath Seats"], strollers) is None


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
    # A list of SaferProducts reports has no amount to chart: ids and ages are not
    # bar heights.
    reports = {"columns": ["incident_report_id", "report_date", "brand",
                           "victim_age_years", "description"],
               "rows": [[6298905, "2026-10-13", "Munchkin", 1, "tipped"],
                        [6298809, "2026-10-11", "Joie", 1, "almost fell"]],
               "truncated": False, "sql": ""}
    assert build_chart(reports, CFG) is None


def _trend_state(sql: str, years: list[int]) -> RunState:
    st = RunState()
    st.record("run_sql", {"query": sql}, json.dumps({
        "columns": ["year", "n_cases", "est_visits"],
        "rows": [[y, 1000, 50000.0] for y in years], "truncated": False}),
        "Product-Injury Detective")
    return st


def test_partial_code_note() -> None:
    """A trend that combines a code missing from some shown years (5045, e-bikes,
    only 2024-2025) gets a note in the answer; codes present every year don't."""
    years = list(range(2019, 2026))
    assert 5045 in DET.partial_codes and 1233 not in DET.partial_codes
    st = _trend_state(
        "SELECT year, COUNT(*) AS n_cases, SUM(weight) AS est_visits FROM neiss_cases "
        "WHERE product_1 IN (5033, 5040, 5045) OR product_2 IN (5033, 5040, 5045) "
        "OR product_3 IN (5033, 5040, 5045) GROUP BY year", years)
    note = partial_code_note(st, DET.partial_codes)
    assert note and "code 5045" in note and "2024-2025" in note, note
    assert "5033" not in note and "5040" not in note, note
    out = add_partial_code_note("Visits went from 417,673 in 2019 to 540,881.\n\n"
                                "- 2019: 417,673\n- 2025: 540,881", note)
    assert out.split("\n\n")[1] == note, "note goes right after the headline"
    # The note says "comparable", so the generic comparability caveat isn't repeated.
    assert not any(c.startswith("Product-code definitions")
                   for c in drop_stated(build_caveats(st, CFG), out))
    # Already explained by the agent: no second note.
    said = "Code 5045 (e-bikes) only appears from 2024, so 2019 is not comparable."
    assert add_partial_code_note(said, note) == said
    # Naming the first year is not an explanation (live crib answer, 2026-11-10).
    crib = ("Cribs went from 55 visits in 2019 to 15,330 in 2025.\n\nCode 1552 went "
            "from 13,025 in 2021 (the first year present in the data) to 15,102.")
    st = _trend_state("SELECT year, SUM(weight) AS est_visits FROM neiss_cases WHERE "
                      "product_1 IN (1529, 1552) GROUP BY year", years)
    out = add_partial_code_note(crib, partial_code_note(st, DET.partial_codes))
    assert "Note: code 1552" in out.split("\n\n")[1], out
    for ok in ("Code 5045 is only present in 2024 and 2025.",
               "E-bikes (code 5045) are present in only 2 of the loaded years.",
               "E-bike code 5045 was excluded to avoid a fake jump."):
        assert add_partial_code_note(ok, note) == ok, ok
    # A plain select of several codes (no SUM/COUNT) lists separate series, even
    # when the code column is left out: no note.
    st = _trend_state("SELECT year, n_cases, est_visits FROM v_product_estimates "
                      "WHERE product_code IN (1529, 1552) ORDER BY product_code, year",
                      years)
    assert partial_code_note(st, DET.partial_codes) is None
    # A code present in every year: no note.
    st = _trend_state("SELECT year, n_cases, est_visits FROM v_product_estimates "
                      "WHERE product_code = 1233 AND age_group = 'all'", years)
    assert partial_code_note(st, DET.partial_codes) is None
    # Only years where 5045 exists: comparable, no note. One year: not a trend.
    st = _trend_state("SELECT year, SUM(est_visits) AS est_visits FROM "
                      "v_product_estimates WHERE product_code IN (5040, 5045) "
                      "GROUP BY year", [2024, 2025])
    assert partial_code_note(st, DET.partial_codes) is None
    st = _trend_state("SELECT year, n_cases, est_visits FROM v_product_estimates "
                      "WHERE product_code = 5045", [2025])
    assert partial_code_note(st, DET.partial_codes) is None
    # A gap in the middle (record players 563: no sampled case in 2023; can openers
    # 401: none in 2022) is a sampling zero, not a coding change: no note.
    st = _trend_state("SELECT year, SUM(est_visits) AS est_visits FROM "
                      "v_product_estimates WHERE product_code IN (563, 401) "
                      "GROUP BY year", years)
    assert partial_code_note(st, DET.partial_codes) is None
    # Separate series per code (cribs 1529 every year, 1552 from 2021): nothing is
    # combined, so no note.
    st = RunState()
    st.record("run_sql", {"query": "SELECT product_code, year, SUM(est_visits) FROM "
                          "v_product_estimates WHERE product_code IN (1529, 1552) "
                          "GROUP BY product_code, year"},
              json.dumps({"columns": ["product_code", "year", "est_visits"],
                          "rows": [[1529, y, 5000.0] for y in years]
                          + [[1552, y, 13000.0] for y in range(2021, 2026)],
                          "truncated": False}), "Product-Injury Detective")
    assert partial_code_note(st, DET.partial_codes) is None
    # Codes with the same years are grouped, with full titles.
    st = _trend_state("SELECT year, SUM(est_visits) AS est_visits FROM "
                      "v_product_estimates WHERE product_code IN (5022, 5023, 5024, "
                      "5025) GROUP BY year", years)
    note = partial_code_note(st, DET.partial_codes)
    assert note and "codes 5022 (Scooters, powered), 5023" in note, note
    assert note.count("2020-2025") == 1, note


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


def test_table_follows_answer() -> None:
    st = RunState()
    st.record("run_sql", {"query": "SELECT body_part, n_cases, est_visits FROM ..."},
              json.dumps({"columns": ["body_part", "n_cases", "est_visits"],
                          "rows": [["Ankle", 601, 19431.2], ["Knee", 357, 11850.4]],
                          "truncated": False}), "Product-Injury Detective")
    st.record("run_sql", {"query": "SELECT age_group, n_cases, est_visits FROM ..."},
              json.dumps({"columns": ["age_group", "n_cases", "est_visits"],
                          "rows": [["0-4", 1108, 27085.0], ["5-14", 2522, 72057.0]],
                          "truncated": False}), "Product-Injury Detective")
    answer = "The ankle leads (19,431 visits), then the knee. By age: 0-4 27,085 ..."
    assert build_table(st, answer)["columns"][0] == "body_part"
    assert build_table(st, "")["columns"][0] == "age_group"  # no numbers -> last
    assert build_table(st, "about 72,000 visits")["columns"][0] == "age_group"


def test_table_follows_small_counts() -> None:
    # Seen live: "Graco (30 reports) ..." but the table showed a later report list.
    st = RunState()
    st.record("run_sql", {"query": "SELECT brand, COUNT(*) AS reports ..."},
              json.dumps({"columns": ["brand", "reports"],
                          "rows": [["Graco", 30], ["Baby Trend", 29]],
                          "truncated": False}), "Product-Injury Detective")
    st.record("run_sql", {"query": "SELECT brand, product_description ..."},
              json.dumps({"columns": ["brand", "product_description"],
                          "rows": [["Joie", "Orzo stroller"]],
                          "truncated": False}), "Product-Injury Detective")
    answer = ("In 2025 the data cannot rank brands for adults 65+. Reports name "
              "Graco (30 reports) and Baby Trend (29 reports).")
    assert build_table(st, answer)["columns"] == ["brand", "reports"]


def test_age_order_and_all_row() -> None:
    st = RunState()
    groups = ["0-4", "15-24", "25-44", "45-64", "5-14", "65+", "all"]  # text order
    st.record("run_sql", {"query": "SELECT age_group, n_cases, est_visits ..."},
              json.dumps({"columns": ["age_group", "n_cases", "est_visits"],
                          "rows": [[g, 100 + i, 1000.0 + i] for i, g in
                                   enumerate(groups)],
                          "truncated": False}), "Product-Injury Detective")
    table = build_table(st)
    assert [r[0] for r in table["rows"]] == [
        "0-4", "5-14", "15-24", "25-44", "45-64", "65+", "all"]
    chart = build_chart(table, CFG)
    assert [d["x"] for d in chart["data"]] == [
        "0-4", "5-14", "15-24", "25-44", "45-64", "65+"]


def test_caveat_not_repeated() -> None:
    answer = "There were 81,016 visits. " + COUNTS_NOT_RATES
    assert drop_stated([COUNTS_NOT_RATES, "Recalls shown are possible matches."],
                       answer) == ["Recalls shown are possible matches."]
    assert drop_stated([COUNTS_NOT_RATES], "There were 81,016 visits.") == [
        COUNTS_NOT_RATES]
    comparab = ("Product-code definitions across years have not been checked against "
                "CPSC's comparability tables, so year-to-year changes may reflect coding.")
    said = "Product-code comparability across years has not been checked, so ..."
    assert drop_stated([comparab], said) == []
    assert drop_stated([comparab], "There were 81,016 visits.") == [comparab]


def test_refusal_first() -> None:
    buried = ("In 2025 there were 16,573 visits.\n\nAs noted, the data cannot answer "
              "which brand is most dangerous.")
    assert refusal_first(buried) == (
        "The data cannot answer which brand is most dangerous.\n\n"
        "In 2025 there were 16,573 visits.")
    first = "The data cannot answer that.\n\nInstead: 5 visits."
    assert refusal_first(first) == first
    assert refusal_first("There were 1,469,070 visits.") == "There were 1,469,070 visits."
    caveat = ("Floors lead with 1,768,793 visits.\n\nThe data does not show that a "
              "recalled product caused these injuries.")
    assert refusal_first(caveat) == caveat  # a closing caveat stays at the end



def test_ensure_cannot() -> None:
    counts = "Bicycles: 540,881 visits; skateboards: 61,011."
    out = ensure_cannot("Is it more dangerous per hour to ride a bike or a skateboard?",
                        counts)
    assert out.startswith("The data cannot answer") and out.endswith(counts)
    said = "The data cannot answer that per hour. Bicycles: 540,881 visits."
    assert ensure_cannot("Is a bike more dangerous per hour?", said) == said
    rate = ensure_cannot("Are adults 65+ more likely to fall on stairs?", counts)
    assert "not rates" in rate
    plain = "How many ER visits were there for children under 5 in 2025?"
    assert ensure_cannot(plain, counts) == counts
    cause = ensure_cannot("Did the recall of infant loungers cause injuries to drop?",
                          "Infant visits: 193,828 in 2019, 258,353 in 2025.")
    assert "caused a change" in cause.split("\n")[0]
    assert ensure_cannot("What causes most falls on stairs?", counts) == counts
    brand = ensure_cannot("Which brand of stroller is the most dangerous?", counts)
    assert brand.startswith("The data cannot answer which brand")
    ohio = ensure_cannot("How many trampoline injuries were there in Ohio in 2024?",
                         "Nationwide in 2024 there were 118,179 visits.", (2019, 2025))
    assert "state, city or hospital" in ohio.split("\n")[0]

def test_grounding_respects_deadline() -> None:
    import asyncio

    st = RunState()
    st.record("run_sql", {"query": "SELECT 1"},
              json.dumps({"columns": ["n"], "rows": [[57638]], "truncated": False}),
              "Product-Injury Detective")

    async def run() -> str:
        past = asyncio.get_running_loop().time() - 1  # no time left: no model call
        class Result:  # stands in for a RunResult; no model is called
            def to_input_list(self) -> list:
                return []

        return await DET._ground(Result(), "There were 9,999 visits.", st, past)

    assert asyncio.run(run()) == "There were 9,999 visits."
    assert st.ungrounded == ["9,999"]
    assert st.tool_calls[-1]["result"] == "rewrite not used"


def _fake_runner(outputs: list[str], calls: list[Any]):
    """A stand-in for Runner.run that returns the given final outputs in order."""

    class Result:
        def __init__(self, text: str) -> None:
            self.final_output, self.new_items = text, []

        def to_input_list(self) -> list:
            return [{"role": "user", "content": "earlier turn"}]

    async def run(agent: Any, inp: Any, **kw: Any) -> Result:
        calls.append((inp, kw.get("session")))
        return Result(outputs[len(calls) - 1])

    return run


def test_empty_answer_retry() -> None:
    """Gemini sometimes ends a turn with no text after a tool call. ask() asks it to
    continue once; it never blames the step limit for an empty answer."""
    import asyncio

    import detective.agent as agent_mod

    real = agent_mod.Runner.run
    answer = "Trampolines were linked to many injury-related ER visits in that year."
    try:
        # Empty, then an answer: the retry's answer is used (with and without session).
        for session in (None, object()):
            calls: list[Any] = []
            agent_mod.Runner.run = _fake_runner(["", answer], calls)
            out = asyncio.run(DET.ask("And for kids under 5 only?", session=session))
            assert out["response"].startswith(answer), out["response"]
            assert len(calls) == 2, calls
            inp, sess = calls[1]
            if session is None:  # no session: the history is passed explicitly
                assert isinstance(inp, list) and inp[0]["content"] == "earlier turn"
                assert "Continue" in inp[-1]["content"]
            else:  # session: only the nudge; the session supplies the history
                assert isinstance(inp, str) and "Continue" in inp and sess is session
        # Empty twice: an honest message, not "I hit my limit".
        calls = []
        agent_mod.Runner.run = _fake_runner(["", ""], calls)
        out = asyncio.run(DET.ask("And for kids under 5 only?"))
        assert out["response"].startswith("I couldn't finish an answer"), out
        assert "limit" not in out["response"] and len(calls) == 2
        # A normal answer: no retry.
        calls = []
        agent_mod.Runner.run = _fake_runner([answer], calls)
        out = asyncio.run(DET.ask("How many trampoline injuries in 2024?"))
        assert out["response"].startswith(answer) and len(calls) == 1
    finally:
        agent_mod.Runner.run = real


def test_app_sessions_and_input() -> None:
    # In a subprocess: importing app opens its own DuckDB connection, and one
    # process can't open the same locked database twice.
    import subprocess
    import sys

    code = """
from fastapi.testclient import TestClient
import app as m
c = TestClient(m.app)
assert c.get("/health").json()["status"] == "ok"
assert c.post("/chat", json={"message": ""}).status_code == 422
assert c.post("/chat", json={"message": "x" * 2001}).status_code == 422
assert c.post("/clear", params={"session_id": "nope"}).json() == {"status": "ok"}
async def fake_ask(message, session=None):  # no model call
    return {"response": "ok", "tool_calls": [], "table": None, "chart": None,
            "caveats": [], "mode": "detective"}
m.DETECTIVE.ask = fake_ask
m.MAX_SESSIONS = 3
ids = [c.post("/chat", json={"message": "hi"}).json()["session_id"] for _ in range(5)]
assert list(m.sessions) == ids[-3:], "oldest sessions are dropped"
c.post("/chat", json={"message": "hi", "session_id": ids[2]})
assert list(m.sessions)[-1] == ids[2], "a used session becomes most recent"
print("ok", len(m.sessions))
"""
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and out.stdout.startswith("ok"), out.stderr[-500:]


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
