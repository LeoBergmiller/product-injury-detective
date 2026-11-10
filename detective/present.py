"""Turn a run's tool results into the response's table, chart spec, and caveats.

Deterministic: the table is the last successful run_sql result; the chart is derived
from it by simple rules; caveats come from what the tools returned.
"""

from __future__ import annotations

import re
from typing import Any

from detective.config import AgentConfig
from detective.tools import RunState

Y_PREFERENCE = ("est_visits", "estimated_visits", "est", "weighted", "visits")
NOT_Y = {"year", "rank", "product_code", "code", "neiss_code", "recall_id", "psu"}
# Unweighted sample counts: never the main series when a weighted column exists.
SAMPLE_COUNT = re.compile(r"^(n|n_cases|cases|sample_cases|case_count|num_cases|n_sample)$")
MAX_LINE_SERIES = 6
LABEL_PREFERENCE = ("product_title", "label", "title", "name", "age_group")
COUNTS_NOT_RATES = (
    "These are counts of injury-related ER visits, not rates: the groups differ in "
    "size, and population denominators are not in the data."
)
AGE_LITERAL = re.compile(r"'(0-4|5-14|15-24|25-44|45-64|65\+|unknown)'")
AGE_YEARS_FILTER = re.compile(
    r"age_years\s*(?:<=?|>=?|=|between)\s*\d+(?:\s+and\s+\d+)?"
)


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def build_table(state: RunState) -> dict[str, Any] | None:
    if not state.sql_results:
        return None
    last = state.sql_results[-1]
    return {
        "columns": last["columns"],
        "rows": last["rows"],
        "truncated": last["truncated"],
        "sql": last["sql"],
    }


def build_chart(
    table: dict[str, Any] | None, cfg: AgentConfig
) -> dict[str, Any] | None:
    if not table or not table["rows"] or len(table["rows"]) < 2:
        return None
    cols: list[str] = table["columns"]
    rows: list[list[Any]] = table["rows"]
    numeric = [
        c for i, c in enumerate(cols)
        if all(_is_num(r[i]) or r[i] is None for r in rows)
        and any(_is_num(r[i]) for r in rows)
    ]
    y_candidates = [c for c in numeric if c.lower() not in NOT_Y]
    if not y_candidates:
        return None
    y = next(
        (c for p in Y_PREFERENCE for c in y_candidates if p in c.lower()),
        y_candidates[-1],
    )
    text = [c for c in cols if c not in numeric]
    label = next((c for p in LABEL_PREFERENCE for c in text if c.lower() == p), None)
    label = label or (text[0] if text else None)
    yi = cols.index(y)

    if "year" in cols and len({r[cols.index("year")] for r in rows}) >= 2:
        xi = cols.index("year")
        series = label if label and len(rows) > len({r[xi] for r in rows}) else None
        # Wide by year (e.g. year, bicycles, e_bicycles, or year, est_visits_0_4,
        # est_visits_65plus): one line per measure column, not just one column.
        preferred = [c for c in y_candidates if any(p in c.lower() for p in Y_PREFERENCE)]
        measures = preferred or [
            c for c in y_candidates if not SAMPLE_COUNT.match(c.lower())
        ]
        if (series is None
                and 2 <= len(measures) <= MAX_LINE_SERIES
                and len(rows) == len({r[xi] for r in rows})):
            data = [
                {"x": r[xi], "y": r[cols.index(c)], "series": c}
                for c in measures for r in rows if r[cols.index(c)] is not None
            ]
            return {"type": "line", "x": "year", "y": "est_visits",
                    "series": "column", "data": data}
        si = cols.index(series) if series else None
        if series and len({r[si] for r in rows}) > 6:
            return None
        data = [
            {"x": r[xi], "y": r[yi], "series": r[si] if si is not None else None}
            for r in rows
            if r[yi] is not None
        ]
        return {"type": "line", "x": "year", "y": y, "series": series, "data": data}

    if label is None or len(rows) > cfg.chart_max_rows:
        return None
    li = cols.index(label)
    # Wide comparison: one row per label, several estimate columns -> grouped bars.
    est_cols = [c for c in y_candidates if any(p in c.lower() for p in Y_PREFERENCE)]
    if len(est_cols) >= 2 and len({r[li] for r in rows}) == len(rows):
        data = [
            {"x": r[li], "y": r[cols.index(c)], "series": c}
            for r in rows for c in est_cols if r[cols.index(c)] is not None
        ]
        return {"type": "bar", "x": label, "y": "est_visits", "series": "column",
                "data": data}
    # Long comparison: label x group (e.g. product x age_group) -> grouped bars.
    others = [c for c in text if c != label]
    group = next(
        (c for c in others if 2 <= len({r[cols.index(c)] for r in rows}) <= 4),
        None,
    )
    if len({r[li] for r in rows}) != len(rows):
        if group is None:
            return None  # labels repeat and nothing to group by
        gi = cols.index(group)
        data = [{"x": r[li], "y": r[yi], "series": r[gi]}
                for r in rows if r[yi] is not None]
        return {"type": "bar", "x": label, "y": y, "series": group, "data": data}
    data = [{"x": r[li], "y": r[yi], "series": None}
            for r in rows if r[yi] is not None]
    return {"type": "bar", "x": label, "y": y, "series": None, "data": data}


def build_caveats(state: RunState, cfg: AgentConfig) -> list[str]:
    caveats: list[str] = []
    sql_all = " ".join(r["sql"] for r in state.sql_results).lower()
    low = False
    years: set[Any] = set()
    for res in state.sql_results:
        cols = [c.lower() for c in res["columns"]]
        if "low_sample" in cols:
            i = cols.index("low_sample")
            low = low or any(r[i] is True for r in res["rows"])
        if "n_cases" in cols:
            i = cols.index("n_cases")
            low = low or any(
                _is_num(r[i]) and r[i] < cfg.min_cases for r in res["rows"]
            )
        if "year" in cols:
            i = cols.index("year")
            years |= {r[i] for r in res["rows"]}
    if low:
        caveats.append(
            f"Some estimates rest on fewer than {cfg.min_cases} sample cases and are "
            "unstable (project threshold)."
        )
    groups = set(AGE_LITERAL.findall(sql_all)) | {
        m.strip() for m in AGE_YEARS_FILTER.findall(sql_all)
    }
    for res in state.sql_results:
        cols = [c.lower() for c in res["columns"]]
        if "age_group" in cols:
            i = cols.index("age_group")
            groups |= {str(r[i]) for r in res["rows"] if r[i] != "all"}
    if len(groups) >= 2 or len(years) >= 2:
        caveats.append(COUNTS_NOT_RATES)
    if len(years) >= 2:
        caveats.append(
            "Product-code definitions across years have not been checked against "
            "CPSC's comparability tables, so year-to-year changes may reflect coding."
        )
    if state.recall_results:
        caveats.append(
            "Recalls shown are possible matches (not reviewed) whose recalled product "
            "matches the NEISS product; a recall does not show that a product caused "
            "these injury-related ER visits."
        )
    if re.search(r"\bsafer_incidents|v_safer_incidents_neiss\b", sql_all):
        caveats.append(
            "SaferProducts.gov reports are voluntary and not representative; they "
            "cannot be turned into national estimates."
        )
    if re.search(r"\bnarrative\b\s+i?like", sql_all):
        caveats.append(
            "Keyword matches in case narratives are approximate: they can miss "
            "spellings and include unrelated mentions."
        )
    return caveats
