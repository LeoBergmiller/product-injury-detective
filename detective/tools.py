"""The agent's tools. Each one returns JSON text; errors are returned, never raised."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from agents import (
    FunctionTool,
    ToolGuardrailFunctionOutput,
    function_tool,
    tool_input_guardrail,
)

from detective.config import AgentConfig
from detective.db import Database, guardrail_message, to_json
from detective.recall_match import (
    belongs_elsewhere,
    keyword_nouns,
    plausible_for_keywords,
    plausible_for_product,
    product_nouns,
    specific_phrases,
)



@dataclass
class RunState:
    """Per-request state shared by hooks, guardrails, and the response builder."""

    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    sql_results: list[dict[str, Any]] = field(default_factory=list)
    recall_results: list[dict[str, Any]] = field(default_factory=list)
    ungrounded: list[str] = field(default_factory=list)

    def record(self, name: str, args: dict[str, Any], result: str, agent: str) -> None:
        self.tool_calls.append(
            {"name": name, "args": args, "result": result, "agent": agent}
        )
        try:
            parsed = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return
        if name == "run_sql" and isinstance(parsed, dict) and parsed.get("rows"):
            self.sql_results.append({"sql": args.get("query", ""), **parsed})
        if name == "search_recalls" and isinstance(parsed, dict):
            self.recall_results.extend(parsed.get("recalls", []))


def _args(raw: str | None) -> dict[str, Any]:
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"_raw": raw}


@tool_input_guardrail
def neiss_rules_guardrail(data: Any) -> ToolGuardrailFunctionOutput:
    """Reject SQL that breaks the data rules (see db.guardrail_message): no table read,
    unweighted counts, raw age filters, summing the long product table, slot 1 only."""
    args = _args(data.context.tool_arguments)
    message = guardrail_message(str(args.get("query", "")))
    if message is None:
        return ToolGuardrailFunctionOutput.allow()
    # A rejected call never reaches on_tool_end, so record it here for the trace.
    state = data.context.context
    if isinstance(state, RunState):
        state.record(
            "run_sql", args, json.dumps({"rejected": message}), data.agent.name
        )
    return ToolGuardrailFunctionOutput.reject_content(message)


def make_run_sql(db: Database, guarded: bool) -> FunctionTool:
    def run_sql(query: str) -> str:
        """Run one read-only DuckDB SELECT and return columns and rows (max 200 rows).

        Errors come back as {"error": ...}: read them, fix the query, and retry.

        Args:
            query: A single DuckDB SELECT statement.
        """
        return to_json(db.query(query))

    guards = [neiss_rules_guardrail] if guarded else []
    return function_tool(run_sql, tool_input_guardrails=guards)


def make_detective_tools(
    db: Database, ctx: dict[str, Any], cfg: AgentConfig
) -> list[FunctionTool]:
    tables: dict[str, Any] = ctx["tables"]
    codebook: dict[str, list[dict[str, Any]]] = ctx["neiss_codebook"]
    safer: dict[str, list[dict[str, Any]]] = ctx["safer_codebook"]
    kinds = (
        ["product", "safer_category", "age_group"]
        + sorted(codebook)
        + sorted(safer)
    )

    @function_tool
    def describe_data(table: str | None = None) -> str:
        """List the tables and views, or show one table's columns with notes.

        Call with no argument first; then call with a table name before writing SQL.

        Args:
            table: A table or view name, e.g. 'v_top_products'. Omit to list all.
        """
        if not table:
            return to_json(
                {
                    "coverage": ctx["coverage"],
                    "objects": [
                        {"name": n, "kind": t["kind"], "description": t["description"]}
                        for n, t in tables.items()
                    ],
                    "tip": "Start with v_top_products / v_product_estimates.",
                }
            )
        t = tables.get(table.strip())
        if t is None:
            return to_json({"error": f"No table '{table}'.", "tables": list(tables)})
        return to_json({"table": table, **t})

    @function_tool
    def lookup_codes(kind: str, query: str | None = None) -> str:
        """Find product codes or the meaning of coded values. Never guess a code.

        Args:
            kind: 'product' (NEISS product codes; pass query words like 'trampoline'),
                'safer_category' (SaferProducts categories), 'age_group', a NEISS
                coded column such as 'body_part', 'diagnosis', 'disposition',
                'location', 'sex', 'race', or a SaferProducts lookup such as
                'SeverityTypes'.
            query: Words to search for (required for 'product' and 'safer_category').
        """
        k = kind.strip()
        if k == "age_group":
            return to_json(
                {
                    "age_group": ctx["age_groups"],
                    "rule": "under 5 = age_years < 5; 'unknown' = age not recorded",
                }
            )
        if k in ("product", "safer_category"):
            if not query:
                return to_json({"error": f"'{k}' needs query words, e.g. 'stairs'."})
            items = ctx["products"] if k == "product" else ctx["safer_categories"]
            key = "title" if k == "product" else "name"
            words = [re.sub(r"s$", "", w) for w in re.findall(r"\w+", query.lower())]
            hits = [
                it
                for it in items
                if (query.strip().isdigit()
                    and str(it.get("code", it.get("id"))) == query.strip())
                or all(w in it[key].lower() for w in words)
            ]
            if not hits:
                return to_json(
                    {"kind": k, "query": query, "matches": [],
                     "hint": "No match. Try one shorter word or a synonym."}
                )
            return to_json(
                {"kind": k, "query": query, "matches": hits[:30],
                 "more": max(0, len(hits) - 30)}
            )
        lookup = codebook.get(k) or next(
            (v for n, v in safer.items() if n.lower() == k.lower()), None
        )
        if lookup is None:
            return to_json({"error": f"Unknown kind '{kind}'.", "kinds": kinds})
        return to_json({"kind": k, "codes": lookup})

    titles = {p["code"]: p["title"] for p in ctx["products"]}
    specific = specific_phrases(titles)

    def _candidates(likes: list[str], year_sql: str, year_par: list[Any]) -> list[
        dict[str, Any]
    ]:
        where = " OR ".join("search_text LIKE ?" for _ in likes)
        return db.rows(
            f"""SELECT recall_id, recall_date, title, hazards_text, url,
                       false AS audited, NULL AS confidence
                FROM recalls WHERE ({where}){year_sql}
                ORDER BY recall_date DESC LIMIT 400""",
            [*likes, *year_par],
        )

    def _names(ids: list[int]) -> dict[int, list[str]]:
        if not ids:
            return {}
        out: dict[int, list[str]] = {}
        for r in db.rows(
            "SELECT recall_id, name FROM recall_products "
            "WHERE recall_id IN (SELECT unnest(?::INTEGER[])) AND name IS NOT NULL",
            [ids],
        ):
            out.setdefault(r["recall_id"], []).append(r["name"])
        return out

    @function_tool
    def search_recalls(
        keywords: str, product_code: int | None = None, since_year: int | None = None
    ) -> str:
        """Find CPSC recalls whose recalled product plausibly is the given product.

        Unrelated text matches (e.g. floor lamps for flooring) are dropped. Every
        result is a "possible match (not reviewed)". If none remain, say "no reviewed
        recall link found".

        Args:
            keywords: Product words, comma-separated, singular,
                e.g. 'bunk bed, loft bed'.
            product_code: Optional NEISS code; then the recalled product must match
                that code's title.
            since_year: Optional earliest recall year, e.g. 2019.
        """
        year_sql = " AND year(recall_date) >= ?" if since_year else ""
        year_par = [since_year] if since_year else []
        terms = keyword_nouns(keywords)
        if product_code is not None:
            title = titles.get(product_code)
            if title is None:
                return to_json({"error": f"Unknown product_code {product_code}. "
                                "Use lookup_codes(kind='product')."})
            nouns = product_nouns(title)
            target = f"NEISS {product_code} ({title})"
            cands = db.rows(
                f"""SELECT recall_id, recall_date, recall_title AS title, hazards_text,
                           url, audited, confidence
                    FROM v_recalls_for_product
                    WHERE neiss_code = ? AND recall_id IS NOT NULL{year_sql}""",
                [product_code, *year_par],
            )
            cands += _candidates([f"%{n}%" for n in sorted(nouns)], year_sql, year_par)
        else:
            if not terms:
                return to_json({"error": "Give at least one keyword of 3+ letters."})
            target = f"keywords '{keywords}'"
            cands = _candidates(
                [f"%{' '.join(w)}%" for _, w in terms] + [f"%{h}%" for h, _ in terms],
                year_sql, year_par,
            )
        seen: dict[int, dict[str, Any]] = {}
        for c in cands:
            seen.setdefault(c["recall_id"], c)
        names = _names(list(seen))
        kept = []
        for rid, c in seen.items():
            pn = names.get(rid, [])
            if c["audited"]:
                why = "reviewed crosswalk link"
            elif product_code is not None:
                noun = plausible_for_product(c["title"], pn, nouns)
                other = belongs_elsewhere(c["title"], pn, product_code, specific)
                why = noun and not other and f"recalled product is a '{noun}'"
            else:
                kw = plausible_for_keywords(c["title"], pn, terms)
                why = kw and f"recalled product is a '{kw}'"
            if why:
                kept.append({
                    "recall_id": rid, "recall_date": c["recall_date"],
                    "title": c["title"], "url": c["url"],
                    "hazards_text": (c.get("hazards_text") or "")[:240],
                    "why": why,
                    "link_status": "possible match (reviewed)" if c["audited"]
                    else "possible match (not reviewed)",
                })
        kept.sort(key=lambda r: str(r["recall_date"]), reverse=True)
        out: dict[str, Any] = {
            "target": target,
            "candidates_checked": len(seen),
            "excluded_unrelated": len(seen) - len(kept),
            "recalls": kept[: cfg.recall_limit],
            "more": max(0, len(kept) - cfg.recall_limit),
        }
        if not kept:
            out["message"] = f"no reviewed recall link found for {target}"
        return to_json(out)

    return [describe_data, lookup_codes, make_run_sql(db, guarded=True), search_recalls]
