"""Build the agent's offline context from docs/SCHEMA.md and the built DuckDB.

Deterministic: same SCHEMA.md + same DB -> byte-identical outputs.
Run after build_db.py.

Writes (paths from build/config.yaml, section `agent`):
- agent_context.json: tables/views with columns and notes, codebooks, product codes,
  SaferProducts categories, coverage. Served by describe_data / lookup_codes.
- system_prompt.md: the rules (template below) + generated catalog + example SQL that
  is executed here, so every example in the prompt is known to run.

Usage: uv run python build/build_agent_context.py [--config build/config.yaml]
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import yaml
from build_db import REPO_ROOT, parse_schema

LOG = logging.getLogger("build_agent_context")

# Objects the agent should start from, in prompt order. Everything else is listed too.
PREFERRED = [
    "v_top_products",
    "v_product_estimates",
    "v_case_products",
    "neiss_cases",
    "v_recalls_for_product",
    "v_safer_incidents_neiss",
]

# Verified at build time (each must return at least one row).
EXAMPLES: list[tuple[str, str]] = [
    (
        "Top products for one age group and year (weighted, any slot, 5555 excluded)",
        """SELECT rank, product_code, product_title, n_cases,
       ROUND(est_visits) AS est_visits, low_sample
FROM v_top_products
WHERE year = 2024 AND age_group = '5-14' AND rank <= 10
ORDER BY rank;""",
    ),
    (
        "One product over the loaded years (state that code comparability is unchecked)",
        """SELECT year, n_cases, ROUND(est_visits) AS est_visits, low_sample
FROM v_product_estimates
WHERE product_code = 1233 AND age_group = 'all'
ORDER BY year;""",
    ),
    (
        "A coded field with labels, for one product (any slot, weighted)",
        """SELECT cb.label AS diagnosis, COUNT(*) AS n_cases,
       ROUND(SUM(p.weight)) AS est_visits
FROM v_case_products p
JOIN neiss_codebook cb ON cb.variable = 'diagnosis' AND cb.code = p.diagnosis
WHERE p.year = 2024 AND p.product_code = 1205
GROUP BY 1 ORDER BY est_visits DESC LIMIT 10;""",
    ),
    (
        "A custom age cut-off (age_years, never age_raw)",
        """SELECT COUNT(*) AS n_cases, ROUND(SUM(weight)) AS est_visits
FROM neiss_cases
WHERE year = 2024 AND age_years < 2;""",
    ),
]

PROMPT_TEMPLATE = """\
You are Product-Injury Detective, a data analyst for parents, caregivers, and people
who look after older adults. You answer questions about which everyday products send
people to US emergency rooms, using a DuckDB database built offline from three CPSC
sources. Every number you state must come from a tool result in this conversation.

## Data loaded
{coverage}

## Tables and views (start with the first ones)
{catalog}

Call describe_data(table) for columns and notes, and lookup_codes(kind, query) for
product codes and coded values. Never guess a code or a column name.

## Rules (the database follows docs/SCHEMA.md; follow these exactly)
1. National numbers are weighted: SUM(weight), or est_visits in the views. COUNT(*) is
   only the sample size; report it as n_cases next to the estimate, never as a total.
   Always say "estimated injury-related ER visits". Never write plain "ER visits",
   "emergency room visits", "injuries", or "people" for an estimate.
2. Age: use age_years or age_group, never age_raw. "Under 5" is age_years < 5 (age_raw
   codes infants as 201-223 months). age_group values: {age_groups}.
3. Products are counted in ANY of the three slots (product_1 is not "the main
   product"). Use v_product_estimates / v_top_products / v_case_products. A case with
   two products counts for both, so never add estimates across products, and never add
   across age_group (it includes an 'all' row). To combine several codes (e.g. all
   bicycle codes), count each case once: SUM(weight) FROM neiss_cases WHERE
   product_1 IN (...) OR product_2 IN (...) OR product_3 IN (...).
4. Code {placeholder} is a placeholder (unknown substance, poisoning, under 5); it is
   already excluded from v_top_products. Exclude it from any ranking you build.
5. If low_sample is true (n_cases < {min_cases}), say the estimate is unstable. This
   threshold is a project rule, not an official CPSC rule.
6. Codes coded 0 are not always "unknown" (body_part 0 = Internal). alcohol, drug and
   fire_involvement = 0 mean "no or not recorded": never report them as a confirmed no.
7. Recalls: call search_recalls once per product (pass its product_code and a short
   keyword), for at most the top 3 products. It only returns recalls whose recalled
   product plausibly is that NEISS product. Each is a "possible match (not reviewed)"
   found by text search{audit_note}: use those words and give the recall URL. If it
   returns no recalls for a product, say "no reviewed recall link found" for it and
   name no recall. A recall is context, not proof that the product caused injuries.
8. SaferProducts reports (v_safer_incidents_neiss) are voluntary and NOT representative.
   Never turn them into national estimates; use them only for examples, brands, models.
9. Trends: {trend_note} Only combine codes that are present in every year shown
   (lookup_codes gives years_present); a code that starts mid-series makes a fake
   jump. For a change over time, give the first and the last year's estimates
   (e.g. "from X in 2019 to Y in 2025"), then the low and high if they matter.
10. Only one statement per run_sql call, and it must be a SELECT. Queries that count
    NEISS rows without the weight are rejected; fix them as the message says.
11. Comparisons between age groups or between years: these are COUNTS, not rates.
    Only when your answer compares two or more groups or years (not for a question
    about one group, even if your query returned several), END the answer (after the
    numbers) with: "These are counts of injury-related ER
    visits, not rates: the groups differ in size, and population denominators are not
    in the data." This sentence never replaces the answer itself. Never
    say one group is "more likely", "at higher risk", or "more vulnerable". Make your
    LAST query return every group in one result, one row per group (a group column,
    n_cases, est_visits), so the page shows them side by side in one table and chart.
12. Grounding: every brand, number, and factual claim in your answer must appear in
    a tool result from this turn. Compute shares and differences in SQL rather than in
    your head. Add no outside knowledge (medical explanations, product facts, brands).
13. Coded labels (diagnosis, body part, disposition, location) are reported exactly as
    the codebook or a tool result gives them. Don't interpret them (e.g. don't say an
    "Internal organ injury" is "largely concussions").

## What the data cannot answer
Only when a question asks for one of the things below, say so in your FIRST
sentence and name the reason; then use at most two queries to show what the data can
say instead. Questions about counts of injury-related ER visits CAN be answered:
answer them directly, without a "cannot answer" disclaimer.
- Risk per use, per hour, or per owner, or which product is "most dangerous": there
  is no exposure data, only counts of injury-related ER visits.
- Which age group or year is riskier, or any rate per person: the data has counts,
  not rates, and population denominators are not in it.
- Which brand or model is most dangerous: NEISS records product types, not brands.
  SaferProducts reports name brands, but they are voluntary and not representative;
  mention a brand only if a tool result in this turn contains it, and never rank
  brands by danger.
- Causation: whether a product, a design, or a recall caused or reduced injuries.
- Places (states, cities, hospitals), rates per population, deaths outside the ER, and
  years outside {year_min}-{year_max}.

## How to answer
- Plan the query, run it, and check the numbers make sense before answering.
- Never end your turn with a plan ("let's check ..."): either call a tool or give the
  final answer.
- lookup_codes takes one short word ("stroller", not "baby strollers"). If no code
  fits after two lookups, say NEISS has no code for that product and stop looking; do
  not search product_codes with run_sql.
- Every answer starts with the direct answer, or for questions the data cannot answer,
  with that statement and the reason. Caveats come after, never instead.
- The page shows the result of your LAST successful run_sql as a table and a chart,
  so make the last query the one that supports your main answer.
- Write a short plain-English answer: the key numbers rounded (e.g. "about 1.47
  million"), the year and age group, n_cases where it matters, and one line on
  limits. No SQL unless asked. No markdown tables.
- If the question is vague (no year or age group), use {year_max} and all ages, and say so.
- Decline anything unrelated to product injuries, recalls, or this data.

## Example queries (verified when this prompt was built)
{examples}
"""


@dataclass(frozen=True)
class ContextConfig:
    schema_md: Path
    db_path: Path
    context_json: Path
    system_prompt: Path
    min_cases: int
    placeholder_code: int

    @classmethod
    def from_yaml(cls, path: Path) -> ContextConfig:
        raw = yaml.safe_load(path.read_text())
        agent, db = raw["agent"], raw["build_db"]
        return cls(
            schema_md=REPO_ROOT / db["schema_md"],
            db_path=REPO_ROOT / agent["db_path"],
            context_json=REPO_ROOT / agent["context_json"],
            system_prompt=REPO_ROOT / agent["system_prompt"],
            min_cases=int(db["min_cases"]),
            placeholder_code=int(raw["product_codes"]["placeholder_codes"][0]),
        )


def table_descriptions(md: Path) -> dict[str, str]:
    """First paragraph under each '### table' heading, plus the Views table rows."""
    out: dict[str, str] = {}
    lines = md.read_text().splitlines()
    for i, line in enumerate(lines):
        h = re.match(r"^### (\w+)\s*$", line)
        if h:
            para: list[str] = []
            for nxt in lines[i + 1 :]:
                if not nxt.strip() or nxt.startswith(("|", "#", "-", "**")):
                    if para:
                        break
                    continue
                para.append(nxt.strip())
            out[h.group(1)] = " ".join(para)
        v = re.match(r"^\| `(\w+)`(?: \(table\))? \| (.+) \|\s*$", line)
        if v:
            out[v.group(1)] = v.group(2).strip()
    return out


def short(text: str, limit: int = 170) -> str:
    """Markdown-free description cut at a sentence boundary (deterministic)."""
    text = re.sub(r"`|\*\*", "", text).strip()
    out = ""
    for sent in re.split(r"(?<=\.)\s+", text):
        if out and len(out) + len(sent) + 1 > limit:
            break
        out = f"{out} {sent}".strip()
    return out if out.endswith(".") else out + "."


def rows(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, Any]]:
    cur = con.execute(sql)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def build(cfg: ContextConfig) -> None:
    schema = parse_schema(cfg.schema_md)
    descs = table_descriptions(cfg.schema_md)
    con = duckdb.connect(str(cfg.db_path), read_only=True)
    con.execute(f"SET file_search_path = '{REPO_ROOT}'")

    objects = rows(
        con,
        "SELECT table_name AS name, table_type FROM information_schema.tables "
        "WHERE table_schema = 'main' ORDER BY table_name",
    )
    names = [o["name"] for o in objects]
    order = [n for n in PREFERRED if n in names] + sorted(
        n for n in names if n not in PREFERRED
    )
    tables: dict[str, Any] = {}
    for name in order:
        cols = rows(con, f"DESCRIBE {name}")
        notes = {c.name: c.notes for c in schema.get(name, [])}
        tables[name] = {
            "kind": "view" if name.startswith("v_") else "table",
            "description": descs.get(name, ""),
            "columns": [
                {
                    "name": c["column_name"],
                    "type": c["column_type"],
                    "notes": notes.get(c["column_name"], ""),
                }
                for c in cols
            ],
        }

    codebook: dict[str, list[dict[str, Any]]] = {}
    for r in rows(
        con, "SELECT variable, code, label, is_unknown FROM neiss_codebook "
        "ORDER BY variable, code"
    ):
        codebook.setdefault(r.pop("variable"), []).append(r)
    safer: dict[str, list[dict[str, Any]]] = {}
    for r in rows(
        con,
        "SELECT lookup, id, public_name FROM safer_codebook "
        "WHERE lookup <> 'ProductCategories' ORDER BY lookup, id",
    ):
        safer.setdefault(r.pop("lookup"), []).append(r)
    products = rows(
        con,
        """SELECT p.product_code AS code, p.product_title AS title, p.is_placeholder,
                  p.is_catch_all, s.n_years AS years_present
           FROM product_codes p LEFT JOIN product_code_stability s USING (product_code)
           ORDER BY p.product_code""",
    )
    sp_categories = rows(
        con,
        """SELECT c.id, c.public_name AS name, x.neiss_code
           FROM safer_codebook c
           LEFT JOIN safer_category_xwalk x ON x.sp_category_id = c.id
           WHERE c.lookup = 'ProductCategories' ORDER BY c.id""",
    )
    age_groups = [
        r["age_group"]
        for r in rows(
            con,
            "SELECT age_group, min(coalesce(age_years, 999)) k FROM neiss_cases "
            "GROUP BY 1 ORDER BY k",
        )
    ]
    year_min, year_max = con.execute(
        "SELECT min(year), max(year) FROM neiss_cases"
    ).fetchall()[0]
    counts = {
        t: con.execute(f"SELECT count(*) FROM {t}").fetchall()[0][0]
        for t in ("neiss_cases", "recalls", "safer_incidents", "product_codes")
    }
    r_min, r_max = con.execute(
        "SELECT min(recall_date), max(recall_date) FROM recalls"
    ).fetchall()[0]
    s_min, s_max = con.execute(
        "SELECT min(report_date), max(report_date) FROM safer_incidents"
    ).fetchall()[0]
    n_checked, n_audited = con.execute(
        "SELECT (SELECT count(*) FROM product_code_stability "
        "WHERE comparability_checked IS NOT NULL), "
        "(SELECT count(*) FROM product_recall_xwalk WHERE audited)"
    ).fetchall()[0]

    examples_md = []
    for title, sql in EXAMPLES:
        n = len(con.execute(sql).fetchall())
        if n == 0:
            raise SystemExit(f"Prompt example returned no rows: {title}")
        examples_md.append(f"-- {title}\n{sql}")
    con.close()

    coverage = (
        f"- NEISS ER cases {year_min}-{year_max}: {counts['neiss_cases']:,} sample "
        "cases from ~100 hospitals, weighted to national estimates.\n"
        f"- CPSC recalls {r_min:%Y-%m-%d} to {r_max:%Y-%m-%d}: {counts['recalls']:,} "
        "recalls (snapshot).\n"
        f"- SaferProducts.gov reports {s_min:%Y-%m-%d} to {s_max:%Y-%m-%d}: "
        f"{counts['safer_incidents']:,} voluntary reports (snapshot).\n"
        f"- {counts['product_codes']:,} NEISS product codes (January 2025 manual)."
    )
    catalog = "\n".join(
        f"- {n} ({t['kind']}): {short(t['description'])}"
        + (
            f" Columns: {', '.join(c['name'] for c in t['columns'])}."
            if n in PREFERRED
            else ""
        )
        for n, t in tables.items()
    )
    trend_note = (
        "product-code comparability across years has NOT been checked (the CPSC "
        "comparability tables are not parsed yet). You may show a product by year, but "
        "say that code definitions may have changed and that a change is not proof of "
        "a real trend."
        if n_checked == 0
        else "use only codes with product_code_stability.comparability_checked = true; "
        "say so for any other code."
    )
    audit_note = (
        "; none have been reviewed by a person yet"
        if n_audited == 0
        else "; only rows with audited = true were reviewed by a person"
    )
    prompt = PROMPT_TEMPLATE.format(
        coverage=coverage,
        catalog=catalog,
        age_groups=", ".join(f"'{a}'" for a in age_groups),
        placeholder=cfg.placeholder_code,
        min_cases=cfg.min_cases,
        audit_note=audit_note,
        trend_note=trend_note,
        year_min=year_min,
        year_max=year_max,
        examples="\n\n".join(examples_md),
    )

    context = {
        "generated_from": ["docs/SCHEMA.md", "data/processed/injuries.duckdb"],
        "coverage": {
            "neiss_years": [year_min, year_max],
            "row_counts": counts,
            "recall_dates": [str(r_min), str(r_max)],
            "safer_report_dates": [str(s_min), str(s_max)],
            "comparability_checked_codes": n_checked,
            "audited_recall_links": n_audited,
        },
        "age_groups": age_groups,
        "min_cases": cfg.min_cases,
        "tables": tables,
        "neiss_codebook": codebook,
        "safer_codebook": safer,
        "products": products,
        "safer_categories": sp_categories,
    }
    cfg.context_json.write_text(
        json.dumps(context, indent=1, sort_keys=False, default=str) + "\n"
    )
    cfg.system_prompt.write_text(prompt)
    LOG.info(
        "wrote %s (%.0f KB, %d objects) and %s (%d chars)",
        cfg.context_json.relative_to(REPO_ROOT),
        cfg.context_json.stat().st_size / 1024,
        len(tables),
        cfg.system_prompt.relative_to(REPO_ROOT),
        len(prompt),
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, default=REPO_ROOT / "build" / "config.yaml")
    build(ContextConfig.from_yaml(ap.parse_args().config))


if __name__ == "__main__":
    main()
