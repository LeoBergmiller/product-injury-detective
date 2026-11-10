"""Turn a run's tool results into the response's table, chart spec, and caveats.

Deterministic: the table is the run_sql result behind the answer's headline number
(else the last one); the chart is derived from it by simple rules; caveats come from
what the tools returned, minus any the answer already states.
"""

from __future__ import annotations

import re
from typing import Any

from detective.config import AgentConfig
from detective.db import filtered_product_codes
from detective.grounding import NUM_COMMA, NUM_MILLION
from detective.tools import RunState

Y_PREFERENCE = ("est_visits", "estimated_visits", "est", "weighted", "visits")
NOT_Y = {"year", "rank", "product_code", "code", "neiss_code", "recall_id", "psu"}
# IDs and ages are attributes of a row, not amounts: charting them as bar heights
# (e.g. SaferProducts incident_report_id or victim_age_years by date) is meaningless.
NOT_Y_PATTERN = re.compile(r"(^|_)id$|_number$|(^|_)age(_|$)")
# Unweighted sample counts: never the main series when a weighted column exists.
SAMPLE_COUNT = re.compile(r"^(n|n_cases|cases|sample_cases|case_count|num_cases|n_sample)$")
MAX_LINE_SERIES = 6
AGE_ORDER = ["0-4", "5-14", "15-24", "25-44", "45-64", "65+", "unknown", "all"]
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


# Small whole counts ("30 reports") for picking the table when the answer has no big
# ones; not part of an age band ("65+", "0-4"), a range, a decimal or a percentage.
SMALL_COUNT = re.compile(r"(?<![\d.,\-–/])(\d{2,})(?![\d,+%]|\.\d|\s*(?:-|–|percent))")


def _answer_numbers(text: str) -> list[tuple[float, bool]]:
    """Counts in the answer, in reading order: (value, visibly rounded). Comma counts
    and "N million"; if there are none, whole numbers of 2+ digits that aren't years."""
    found = [
        (m.start(), float(m.group(1).replace(",", "")), m.group(1).endswith(",000"))
        for m in NUM_COMMA.finditer(text)
    ]
    found += [(m.start(), float(m.group(1)) * 1e6, True)
              for m in NUM_MILLION.finditer(text)]
    if not found:
        found = [(m.start(), float(m.group(1)), False)
                 for m in SMALL_COUNT.finditer(text)
                 if not 1900 <= int(m.group(1)) <= 2100]
    return [(v, rounded) for _, v, rounded in sorted(found)]


def _holds(res: dict[str, Any], v: float, rounded: bool) -> bool:
    tol = max(1.0, 0.005 * abs(v)) if rounded else 1.0
    return any(_is_num(x) and abs(x - v) <= tol for row in res["rows"] for x in row)


def _age_ordered(columns: list[str], rows: list[list[Any]]) -> list[list[Any]]:
    """One row per age group in text order ('15-24' before '5-14') -> age order."""
    if "age_group" not in columns:
        return rows
    i = columns.index("age_group")
    groups = [r[i] for r in rows]
    if (len(set(groups)) != len(groups) or not set(groups) <= set(AGE_ORDER)
            or groups != sorted(groups)):
        return rows  # repeated groups, other labels, or a deliberate ORDER BY
    return sorted(rows, key=lambda r: AGE_ORDER.index(r[i]))


def build_table(state: RunState, response: str = "") -> dict[str, Any] | None:
    """The query result behind the answer's headline number.

    That is the latest result holding the answer's first count (checking the first
    three): comma counts and "N million", or, if the answer has none, whole numbers of
    2+ digits that aren't years ("30 reports"). With no match, the last result. The
    agent sometimes runs an extra query after the one it answers from, so "last" alone
    can show the wrong table.
    """
    if not state.sql_results:
        return None
    chosen = state.sql_results[-1]
    for v, rounded in _answer_numbers(response)[:3]:
        hits = [r for r in state.sql_results if _holds(r, v, rounded)]
        if hits:
            chosen = hits[-1]
            break
    return {
        "columns": chosen["columns"],
        "rows": _age_ordered(chosen["columns"], chosen["rows"]),
        "truncated": chosen["truncated"],
        "sql": chosen["sql"],
    }


def build_chart(
    table: dict[str, Any] | None, cfg: AgentConfig
) -> dict[str, Any] | None:
    if not table or not table["rows"] or len(table["rows"]) < 2:
        return None
    cols: list[str] = table["columns"]
    rows: list[list[Any]] = table["rows"]
    if "age_group" in cols:
        # The 'all' total next to its own age groups dwarfs them; the table keeps it.
        ai = cols.index("age_group")
        parts = [r for r in rows if r[ai] != "all"]
        if parts and len(parts) < len(rows):
            rows = parts
        if len(rows) < 2:
            return None
    numeric = [
        c for i, c in enumerate(cols)
        if all(_is_num(r[i]) or r[i] is None for r in rows)
        and any(_is_num(r[i]) for r in rows)
    ]
    y_candidates = [c for c in numeric
                    if c.lower() not in NOT_Y and not NOT_Y_PATTERN.search(c.lower())]
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


AGGREGATE = re.compile(r"\b(sum|count)\s*\(", re.IGNORECASE)
EXPLAINED = (
    r"not comparable|fake jump|exclud|"
    r"only (?:be )?(?:present|appears?|available|found|exists?|coded|in use)|"
    r"(?:present|appears?|available) (?:only|in only)"
)


def _year_span(years: list[int]) -> str:
    ys = sorted(years)
    return f"{ys[0]}" if len(ys) == 1 else f"{ys[0]}-{ys[-1]}"


def _contiguous(years: list[int]) -> bool:
    ys = sorted(years)
    return ys == list(range(ys[0], ys[-1] + 1))


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def partial_code_note(
    state: RunState, partial_codes: dict[int, tuple[str | None, list[int]]]
) -> str | None:
    """A sentence for the answer when a result across years filters on a product code
    that is in use for only part of those years (e.g. 5045, e-bikes, only from 2024).

    The system prompt asks the agent not to combine such codes, but it sometimes does
    (bicycles 5033+5040+5045 "rose" from 2019 to 2025 partly because 5045 is new), so
    this check runs in code. partial_codes maps each code not present in every loaded
    year to (title, years_present), from product_code_stability.

    Only codes present in one unbroken run of years (introduced or retired) count. A
    small code with a gap in the middle (record players, no sampled case in 2023) is
    a sampling zero, not a coding change.
    """
    flagged: dict[int, tuple[str | None, list[int]]] = {}
    for res in state.sql_results:
        cols = [c.lower() for c in res["columns"]]
        # Only aggregates can combine codes into one total; a plain select from
        # v_product_estimates lists each code's own rows (even without its column).
        if "year" not in cols or not AGGREGATE.search(res["sql"]):
            continue
        i = cols.index("year")
        shown_all = {r[i] for r in res["rows"] if isinstance(r[i], int)}
        if len(shown_all) < 2:
            continue
        # One row per code and year (separate series, not a combined total): each
        # code's years come from its own rows.
        k = next((cols.index(c) for c in ("product_code", "code", "neiss_code")
                  if c in cols), None)
        for code in filtered_product_codes(res["sql"]):
            if code not in partial_codes:
                continue
            shown = shown_all if k is None else {
                r[i] for r in res["rows"] if r[k] == code and isinstance(r[i], int)}
            title, present = partial_codes[code]
            # Missing in some shown years, but contributing to at least one.
            if _contiguous(present) and shown - set(present) and shown & set(present):
                flagged[code] = (title, present)
    if not flagged:
        return None
    by_span: dict[str, list[str]] = {}
    for code, (title, present) in sorted(flagged.items()):
        name = re.split(r"\s+\(", title, maxsplit=1)[0] if title else None
        by_span.setdefault(_year_span(present), []).append(
            f"{code} ({name})" if name else str(code))
    parts = [
        ("code " if len(codes) == 1 else "codes ") + _join(codes)
        + (" has" if len(codes) == 1 else " have") + f" cases only in {span}"
        for span, codes in by_span.items()
    ]
    return (
        "Note: " + "; ".join(parts) + " among the loaded years. Totals that include "
        + ("it" if len(flagged) == 1 else "them")
        + " are not comparable across years: part of any change over time may reflect "
        "when a product code was in use, not a change in injuries."
    )


def add_partial_code_note(response: str, note: str | None) -> str:
    """Put the note after the first paragraph (where the headline change is), unless
    the answer already says, for every flagged code, that it is only present in some
    years or that totals with it are not comparable. Saying a code's first year is
    not enough: "from 55 in 2019 to 15,330 in 2025" with "1552 ... 2021 (the first
    year present)" still reads as a real jump."""
    if not note:
        return response
    codes = [c for c in re.findall(r"\b\d{2,4}\b", note) if not 1900 <= int(c) <= 2100]
    said = " ".join(response.split())
    if all(re.search(rf"\b{c}\b[^.]*({EXPLAINED})", said, re.IGNORECASE)
           for c in codes):
        return response
    head, sep, rest = response.strip().partition("\n\n")
    return head + "\n\n" + note + (sep + rest if rest else "")


def drop_stated(caveats: list[str], response: str) -> list[str]:
    """Drop caveats the answer already states (the prompt asks the agent to end
    comparisons with the counts-not-rates sentence itself), so it isn't shown twice."""
    said = " ".join(response.lower().split())
    return [c for c in caveats
            if re.split(r"[:;.]", c, maxsplit=1)[0].lower() not in said
            and not (c.startswith("Product-code definitions") and "comparab" in said)]
