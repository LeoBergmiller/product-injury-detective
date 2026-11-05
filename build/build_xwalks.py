"""Build safer_category_xwalk and product_recall_xwalk (docs/SCHEMA.md).

safer_category_xwalk: SaferProducts category -> NEISS code by EXACT normalized name
  (lowercase; runs of non-alphanumerics -> one space; trimmed). SP has duplicate
  categories (old id 121-890 + new id 1105-2248, same name); all map to the one NEISS
  code (many-to-one). The build FAILS if any SP name maps to more than one NEISS code.

product_recall_xwalk: a TEXT-based DRAFT for human review. Scope: union of the top-N
  NEISS codes by weighted visits (any slot, placeholder and untitled codes excluded)
  for each scope group (all ages, under 5, 65+). Candidates:
    - text_search: title-derived terms matched (word boundaries) in recall text.
        high   = term in the recall title
        medium = term in a recall product name / category name only
        low    = term only in hazards / description
    - sp_category_id: recall Products[].CategoryID (older recalls only) ->
        safer_category_xwalk -> NEISS code. confidence = medium.
  Nothing is verified: `audited` is always false in the draft.
  The draft is written to draft_csv every run. seed_csv (the human-reviewed copy) is
  created from the draft only if it does not exist yet, and is never overwritten.
  The Parquet is built from seed_csv.

Usage: uv run python build/build_xwalks.py [--config build/config.yaml]
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import yaml

LOG = logging.getLogger("build_xwalks")
REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_COLS = ["neiss_code", "recall_id", "search_terms", "match_method",
                 "confidence", "audited", "scope_groups", "notes"]
REVIEW_COLS = ["product_title", "recall_date", "recall_title", "matched_in"]
CONF_RANK = {"high": 3, "medium": 2, "low": 1}
# A title match followed by one of these words reads as the product itself
# ("Bunk Beds Due to ..."). Followed by any other word, the term is probably a
# modifier ("Floor Lamps", "Step Stools") and the match is downgraded to medium.
BOUNDARY_WORDS = {
    "due", "recall", "recalls", "recalled", "and", "sold", "because", "with", "for",
    "by", "from", "in", "to", "at", "posing", "pose", "poses", "prompts", "are", "is",
    "after", "over", "that", "which", "following", "may", "can", "could", "of",
}
NEXT_WORD_RE = re.compile(r"\s*([a-z]+)")


@dataclass(frozen=True)
class XwalkConfig:
    out_dir: Path
    seed_csv: Path
    draft_csv: Path
    top_n: int
    scope_groups: dict[str, str]
    stop_phrases: list[str]
    min_term_len: int
    max_lowconf_per_code: int

    @classmethod
    def from_yaml(cls, path: Path) -> XwalkConfig:
        raw = yaml.safe_load(path.read_text())["xwalks"]
        return cls(
            out_dir=REPO_ROOT / raw["out_dir"],
            seed_csv=REPO_ROOT / raw["seed_csv"],
            draft_csv=REPO_ROOT / raw["draft_csv"],
            top_n=int(raw["top_n"]),
            scope_groups=dict(raw["scope_groups"]),
            stop_phrases=sorted(raw["stop_phrases"], key=len, reverse=True),
            min_term_len=int(raw["min_term_len"]),
            max_lowconf_per_code=int(raw["max_lowconf_per_code"]),
        )


def norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def singular(term: str) -> str | None:
    last = term.split(" ")[-1]
    if last.endswith("ies") and len(last) > 4:
        return term[: -3] + "y"
    if last.endswith("s") and not last.endswith("ss") and len(last) > 3:
        return term[:-1]
    return None


def derive_terms(title: str, cfg: XwalkConfig) -> list[str]:
    """Search terms from a NEISS title: drop parentheticals and generic phrases,
    split on commas / 'or' / 'and' / slashes, add simple singular forms."""
    t = re.sub(r"\([^)]*\)", " ", title.lower())
    for sp in cfg.stop_phrases:
        t = t.replace(sp, " ")
    terms: list[str] = []
    for part in re.split(r",|;|/|\bor\b|\band\b", t):
        p = re.sub(r"^(including|excluding|excl|incl)\b\.?", " ", part.strip())
        p = re.sub(r"[^a-z0-9' -]", " ", p)
        p = re.sub(r"\s+", " ", p).strip(" -'")
        if len(p) >= cfg.min_term_len and p not in terms:
            terms.append(p)
    for t0 in list(terms):
        s = singular(t0)
        if s and len(s) >= 3 and s not in terms:
            terms.append(s)
    return terms


# ------------------------------------------------------------ safer_category_xwalk
def build_safer_xwalk(con: duckdb.DuckDBPyConnection, out: Path) -> None:
    neiss = con.sql("SELECT product_code, product_title FROM product_codes").fetchall()
    by_name: dict[str, set[int]] = defaultdict(set)
    for code, title in neiss:
        by_name[norm(title)].add(code)
    clash = {n: c for n, c in by_name.items() if len(c) > 1}
    if clash:
        raise SystemExit(f"NEISS titles collide after normalization: {clash}")
    sp = con.sql(
        "SELECT id, public_name FROM safer_codebook WHERE lookup = 'ProductCategories'"
    ).fetchall()
    sp_by_name: dict[str, list[int]] = defaultdict(list)
    for sid, name in sp:
        sp_by_name[norm(name)].append(sid)
    rows: list[tuple[Any, ...]] = []
    for sid, name in sp:
        codes = by_name.get(norm(name), set())
        if len(codes) > 1:
            raise SystemExit(f"SP category {sid} {name!r} maps to NEISS codes {codes}")
        if codes:
            dup = len(sp_by_name[norm(name)]) > 1
            note = (f"same name as SP ids {sorted(sp_by_name[norm(name)])}"
                    if dup else None)
            rows.append((sid, next(iter(codes)), "exact_name", dup, note))
    con.execute(
        """CREATE OR REPLACE TABLE safer_category_xwalk (sp_category_id INTEGER,
           neiss_code INTEGER, match_method VARCHAR, sp_duplicate_name BOOLEAN,
           notes VARCHAR)"""
    )
    con.executemany("INSERT INTO safer_category_xwalk VALUES (?, ?, ?, ?, ?)", rows)
    n_codes, n_dup, inc_cov = con.sql(
        """SELECT count(DISTINCT neiss_code), count(*) FILTER (WHERE sp_duplicate_name),
           (SELECT round(100.0 * count(x.sp_category_id) / count(*), 1)
            FROM safer_incidents i LEFT JOIN safer_category_xwalk x
            ON i.sp_category_id = x.sp_category_id)
           FROM safer_category_xwalk"""
    ).fetchall()[0]
    LOG.info(
        "safer_category_xwalk: %d SP ids -> %d NEISS codes; %d duplicate-name SP ids; "
        "%.1f%% of incidents linked",
        len(rows), n_codes, n_dup, inc_cov,
    )
    con.execute(
        f"COPY (SELECT * FROM safer_category_xwalk ORDER BY sp_category_id) "
        f"TO '{out}' (FORMAT parquet, COMPRESSION zstd)"
    )


# ------------------------------------------------------------ product_recall_xwalk
def top_codes(con: duckdb.DuckDBPyConnection, cfg: XwalkConfig) -> dict[int, list[str]]:
    scope: dict[int, list[str]] = defaultdict(list)
    for group, pred in cfg.scope_groups.items():
        rows = con.sql(
            f"""
            SELECT p.product_code, sum(c.weight) est
            FROM neiss_case_products p JOIN neiss_cases c USING (case_id)
            JOIN product_codes pc USING (product_code)
            WHERE ({pred}) AND NOT pc.is_placeholder
            GROUP BY 1 ORDER BY est DESC LIMIT {cfg.top_n}
            """
        ).fetchall()
        for code, _ in rows:
            scope[code].append(group)
        LOG.info("Top %d for %s: first 5 = %s", cfg.top_n, group,
                 [r[0] for r in rows[:5]])
    LOG.info("Union of scope groups: %d codes", len(scope))
    return scope


def recall_candidates(
    con: duckdb.DuckDBPyConnection, cfg: XwalkConfig, scope: dict[int, list[str]]
) -> list[dict[str, Any]]:
    recalls = con.sql(
        """SELECT r.recall_id, r.recall_date, r.title, r.search_text,
                  string_agg(coalesce(p.name, '') || ' '
                             || coalesce(p.category_name, ''), ' ') AS prod_text
           FROM recalls r LEFT JOIN recall_products p USING (recall_id)
           GROUP BY ALL"""
    ).fetchall()
    titles = dict(con.sql("SELECT product_code, product_title FROM product_codes")
                  .fetchall())
    chain = defaultdict(set)
    for code, rid in con.sql(
        """SELECT DISTINCT x.neiss_code, p.recall_id FROM recall_products p
           JOIN safer_category_xwalk x ON p.category_id = x.sp_category_id"""
    ).fetchall():
        chain[code].add(rid)
    meta = {r[0]: (r[1], r[2]) for r in recalls}

    out: list[dict[str, Any]] = []
    for code in sorted(scope):
        title = titles[code]
        terms = derive_terms(title, cfg)
        groups = ",".join(scope[code])
        found: dict[int, dict[str, Any]] = {}
        if terms:
            rx = re.compile(r"\b(" + "|".join(re.escape(t) for t in terms) + r")\b")
            for rid, rdate, rtitle, stext, ptext in recalls:
                tl = (rtitle or "").lower()
                tm = rx.search(tl)
                if tm:
                    nxt = NEXT_WORD_RE.match(tl, tm.end())
                    if nxt and nxt.group(1) not in BOUNDARY_WORDS:
                        conf = "medium"
                        where = f"title, as modifier ('{tm.group(0)} {nxt.group(1)}')"
                    else:
                        conf, where = "high", "title"
                elif rx.search((ptext or "").lower()):
                    conf, where = "medium", "product/category name"
                elif rx.search(stext or ""):
                    conf, where = "low", "hazard/description"
                else:
                    continue
                found[rid] = {"confidence": conf, "matched_in": where,
                              "match_method": "text_search", "notes": None}
        for rid in chain.get(code, set()):
            if rid in found:
                found[rid]["notes"] = "also via sp_category_id"
                if CONF_RANK[found[rid]["confidence"]] < CONF_RANK["medium"]:
                    found[rid]["confidence"] = "medium"
            else:
                found[rid] = {"confidence": "medium", "matched_in": "category id chain",
                              "match_method": "sp_category_id",
                              "notes": "recall CategoryID -> SP name -> NEISS title"}
        high = [r for r in found if found[r]["confidence"] == "high"]
        rest = sorted((r for r in found if found[r]["confidence"] != "high"),
                      key=lambda r: (CONF_RANK[found[r]["confidence"]], meta[r][0]),
                      reverse=True)
        kept = high + rest[: cfg.max_lowconf_per_code]
        counts = {c: sum(1 for r in found if found[r]["confidence"] == c)
                  for c in CONF_RANK}
        out.append({
            "neiss_code": code, "recall_id": None, "search_terms": "; ".join(terms),
            "match_method": "text_search", "confidence": "low", "audited": False,
            "scope_groups": groups,
            "notes": (f"terms-only row; candidates high={counts['high']} "
                      f"medium={counts['medium']} low={counts['low']}; "
                      f"medium/low capped at {cfg.max_lowconf_per_code}"),
            "product_title": title, "recall_date": None, "recall_title": None,
            "matched_in": None,
        })
        for rid in sorted(kept, key=lambda r: (meta[r][0], r), reverse=True):
            f = found[rid]
            out.append({
                "neiss_code": code, "recall_id": rid, "search_terms": "; ".join(terms),
                "match_method": f["match_method"], "confidence": f["confidence"],
                "audited": False, "scope_groups": groups, "notes": f["notes"],
                "product_title": title, "recall_date": meta[rid][0],
                "recall_title": meta[rid][1], "matched_in": f["matched_in"],
            })
    return out


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CONTRACT_COLS + REVIEW_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r[k] is None else r[k]) for k in w.fieldnames})


def build_recall_xwalk_parquet(con: duckdb.DuckDBPyConnection, cfg: XwalkConfig,
                               out: Path) -> None:
    con.execute(
        f"""
        CREATE OR REPLACE TABLE product_recall_xwalk AS
        SELECT CAST(neiss_code AS INTEGER) AS neiss_code,
               CAST(NULLIF(recall_id, '') AS INTEGER) AS recall_id,
               search_terms, match_method, confidence,
               CAST(lower(audited) = 'true' AS BOOLEAN) AS audited,
               scope_groups, NULLIF(notes, '') AS notes
        FROM read_csv('{cfg.seed_csv}', header=true, all_varchar=true)
        """
    )
    checks = {
        "confidence values": "SELECT confidence FROM product_recall_xwalk WHERE "
                             "confidence NOT IN ('high','medium','low')",
        "match_method values": "SELECT match_method FROM product_recall_xwalk WHERE "
                               "match_method NOT IN ('text_search','manual',"
                               "'sp_category_id')",
        "audited parsed": "SELECT audited FROM product_recall_xwalk "
                          "WHERE audited IS NULL",
        "neiss_code FK": "SELECT neiss_code FROM product_recall_xwalk "
                         "ANTI JOIN product_codes ON neiss_code = product_code",
        "recall_id FK": "SELECT recall_id FROM product_recall_xwalk x WHERE "
                        "recall_id IS NOT NULL AND recall_id NOT IN "
                        "(SELECT recall_id FROM recalls)",
        "unique (neiss_code, recall_id)": "SELECT neiss_code, recall_id FROM "
                                          "product_recall_xwalk GROUP BY ALL "
                                          "HAVING count(*) > 1",
    }
    for name, sql in checks.items():
        bad = con.sql(sql).fetchall()
        if bad:
            raise SystemExit(f"product_recall_xwalk check failed ({name}): {bad[:5]}")
    stats = con.sql(
        """SELECT count(*), count(DISTINCT neiss_code),
                  count(*) FILTER (WHERE recall_id IS NOT NULL),
                  count(*) FILTER (WHERE audited) FROM product_recall_xwalk"""
    ).fetchall()[0]
    LOG.info("product_recall_xwalk from seed: rows=%d codes=%d candidate links=%d "
             "audited=%d", *stats)
    con.execute(
        f"COPY (SELECT * FROM product_recall_xwalk ORDER BY neiss_code, recall_id "
        f"NULLS FIRST) TO '{out}' (FORMAT parquet, COMPRESSION zstd)"
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", type=Path, default=REPO_ROOT / "build/config.yaml")
    cfg = XwalkConfig.from_yaml(ap.parse_args().config)
    d = cfg.out_dir
    con = duckdb.connect()
    for t, src in [
        ("product_codes", f"read_parquet('{d}/product_codes.parquet')"),
        ("safer_codebook", f"read_parquet('{d}/safer_codebook.parquet')"),
        ("safer_incidents", f"read_parquet('{d}/safer_incidents.parquet')"),
        ("recalls", f"read_parquet('{d}/recalls.parquet')"),
        ("recall_products", f"read_parquet('{d}/recall_products.parquet')"),
        ("neiss_cases", f"read_parquet('{d}/neiss_cases/*.parquet')"),
        ("neiss_case_products", f"read_parquet('{d}/neiss_case_products.parquet')"),
    ]:
        con.execute(f"CREATE VIEW {t} AS SELECT * FROM {src}")

    build_safer_xwalk(con, d / "safer_category_xwalk.parquet")
    scope = top_codes(con, cfg)
    rows = recall_candidates(con, cfg, scope)
    write_csv(rows, cfg.draft_csv)
    LOG.info("Wrote draft %s (%d rows)",
             cfg.draft_csv.relative_to(REPO_ROOT), len(rows))
    if not cfg.seed_csv.exists():
        shutil.copyfile(cfg.draft_csv, cfg.seed_csv)
        LOG.info("Seed did not exist: created %s from draft",
                 cfg.seed_csv.relative_to(REPO_ROOT))
    else:
        LOG.info("Seed exists: NOT overwritten (diff against the draft to review)")
    build_recall_xwalk_parquet(con, cfg, d / "product_recall_xwalk.parquet")


if __name__ == "__main__":
    main()
