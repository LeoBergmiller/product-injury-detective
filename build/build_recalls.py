"""Build recalls and recall_products Parquet (docs/SCHEMA.md) from the API snapshot.

- Pseudo-recalls (RecallID 0 / Title "Error retrieving ...") fail the build: the
  fetcher already rejects them, so finding one here means the snapshot is corrupt.
- Products[].CategoryID / Type: empty for every recall dated 2017-2025, filled for most
  older ones (and some 2026). Where filled they are SaferProducts category ids (NOT
  NEISS codes). Kept as nullable recall_products.category_id / category_name
  (additive deviation; see docs/SCHEMA.md changelog).
- search_text = lowercased title + product names + category names + hazards +
  description, for keyword search (v_recall_search).

Usage: uv run python build/build_recalls.py [--config build/config.yaml]
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import yaml

LOG = logging.getLogger("build_recalls")
REPO_ROOT = Path(__file__).resolve().parent.parent
COMPANY_KEYS = ["Manufacturers", "Retailers", "Importers", "Distributors"]


@dataclass(frozen=True)
class RecallsBuildConfig:
    snapshot_dir: Path
    out_dir: Path
    tmp_dir: Path
    joiner: str

    @classmethod
    def from_yaml(cls, path: Path) -> RecallsBuildConfig:
        raw = yaml.safe_load(path.read_text())["recalls"]
        return cls(
            snapshot_dir=REPO_ROOT / raw["snapshot_dir"],
            out_dir=REPO_ROOT / raw["out_dir_processed"],
            tmp_dir=REPO_ROOT / raw["tmp_dir"],
            joiner=raw["text_joiner"],
        )


def clean(s: Any) -> str | None:
    s = str(s).strip() if s is not None else ""
    return s or None


def names(items: list[dict[str, Any]] | None, key: str = "Name") -> list[str]:
    return [n for n in (clean(i.get(key)) for i in items or []) if n]


def to_int(s: Any) -> int | None:
    try:
        return int(str(s).strip())
    except (TypeError, ValueError):
        return None


def flatten(cfg: RecallsBuildConfig) -> tuple[list[dict], list[dict], dict[str, int]]:
    recalls: list[dict] = []
    products: list[dict] = []
    per_file: dict[str, int] = {}
    seen: set[int] = set()
    j = cfg.joiner
    for path in sorted(cfg.snapshot_dir.glob("recalls_*.json")):
        data = json.loads(path.read_bytes())
        per_file[path.stem] = len(data)
        for r in data:
            rid = r.get("RecallID")
            title = clean(r.get("Title")) or ""
            if rid == 0 or title.startswith("Error retrieving"):
                raise SystemExit(f"{path.name}: error pseudo-recall in snapshot")
            if rid in seen:
                LOG.warning("Duplicate RecallID %s in %s; kept first", rid, path.name)
                continue
            seen.add(rid)
            prods = r.get("Products") or []
            pnames = names(prods)
            cnames = names(prods, "Type")
            hazards = names(r.get("Hazards"))
            desc = clean(r.get("Description"))
            companies = [n for k in COMPANY_KEYS for n in names(r.get(k))]
            recalls.append(
                {
                    "recall_id": rid,
                    "recall_number": clean(r.get("RecallNumber")),
                    "recall_date": (r.get("RecallDate") or "")[:10] or None,
                    "last_publish_date": (r.get("LastPublishDate") or "")[:10] or None,
                    "title": title,
                    "description": desc,
                    "url": clean(r.get("URL")),
                    "hazards_text": j.join(hazards) or None,
                    "injuries_text": j.join(names(r.get("Injuries"))) or None,
                    "remedies_text": j.join(names(r.get("Remedies"))) or None,
                    "companies_text": j.join(companies) or None,
                    "search_text": " ".join(
                        [title, *pnames, *cnames, *hazards, desc or ""]
                    ).lower(),
                }
            )
            for seq, p in enumerate(prods, start=1):
                products.append(
                    {
                        "recall_id": rid,
                        "product_seq": seq,
                        "name": clean(p.get("Name")),
                        "description": clean(p.get("Description")),
                        "model": clean(p.get("Model")),
                        "units_text": clean(p.get("NumberOfUnits")),
                        "category_id": to_int(p.get("CategoryID")),
                        "category_name": clean(p.get("Type")),
                    }
                )
    return recalls, products, per_file


def write_ndjson(rows: list[dict], path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def build(cfg: RecallsBuildConfig) -> None:
    cfg.tmp_dir.mkdir(parents=True, exist_ok=True)
    recalls, products, per_file = flatten(cfg)
    LOG.info("Flattened %d recalls, %d products from %d files",
             len(recalls), len(products), len(per_file))
    r_nd, p_nd = cfg.tmp_dir / "recalls.ndjson", cfg.tmp_dir / "recall_products.ndjson"
    write_ndjson(recalls, r_nd)
    write_ndjson(products, p_nd)

    con = duckdb.connect()
    con.execute(
        f"""
        CREATE TABLE recalls AS
        SELECT CAST(recall_id AS INTEGER) AS recall_id, recall_number,
               CAST(recall_date AS DATE) AS recall_date,
               CAST(last_publish_date AS DATE) AS last_publish_date,
               title, description, url, hazards_text, injuries_text, remedies_text,
               companies_text, search_text
        FROM read_json('{r_nd}', format='newline_delimited', columns={{
          'recall_id': 'BIGINT', 'recall_number': 'VARCHAR', 'recall_date': 'VARCHAR',
          'last_publish_date': 'VARCHAR', 'title': 'VARCHAR', 'description': 'VARCHAR',
          'url': 'VARCHAR', 'hazards_text': 'VARCHAR', 'injuries_text': 'VARCHAR',
          'remedies_text': 'VARCHAR', 'companies_text': 'VARCHAR',
          'search_text': 'VARCHAR'}})
        ORDER BY recall_date, recall_id
        """
    )
    con.execute(
        f"""
        CREATE TABLE recall_products AS
        SELECT CAST(recall_id AS INTEGER) AS recall_id,
               CAST(product_seq AS SMALLINT) AS product_seq,
               name, description, model, units_text,
               CAST(category_id AS INTEGER) AS category_id, category_name
        FROM read_json('{p_nd}', format='newline_delimited', columns={{
          'recall_id': 'BIGINT', 'product_seq': 'BIGINT', 'name': 'VARCHAR',
          'description': 'VARCHAR', 'model': 'VARCHAR', 'units_text': 'VARCHAR',
          'category_id': 'BIGINT', 'category_name': 'VARCHAR'}})
        ORDER BY recall_id, product_seq
        """
    )
    bad = con.sql(
        "SELECT count(*) FILTER (WHERE recall_date IS NULL), "
        "count(*) FILTER (WHERE recall_number IS NULL) FROM recalls"
    ).fetchall()[0]
    if any(bad):
        raise SystemExit(f"recalls: null recall_date/recall_number counts: {bad}")
    mism = con.sql(
        "SELECT count(*) FROM recall_products p ANTI JOIN recalls r USING (recall_id)"
    ).fetchall()[0][0]
    if mism:
        raise SystemExit(f"recall_products rows without a recall: {mism}")
    by_year = con.sql(
        """SELECT year(r.recall_date) y, count(DISTINCT r.recall_id),
                  count(p.recall_id) FILTER (WHERE p.category_id IS NOT NULL)
           FROM recalls r LEFT JOIN recall_products p USING (recall_id)
           GROUP BY y ORDER BY y"""
    ).fetchall()
    LOG.info("Recalls per year (year, recalls, products with category_id): %s", by_year)

    for table in ["recalls", "recall_products"]:
        out = cfg.out_dir / f"{table}.parquet"
        con.execute(f"COPY {table} TO '{out}' (FORMAT parquet, COMPRESSION zstd)")
        n = con.sql(f"SELECT count(*) FROM {table}").fetchall()[0][0]
        LOG.info("Wrote %s: %d rows, %.1f MB", out.name, n, out.stat().st_size / 1e6)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", type=Path, default=REPO_ROOT / "build/config.yaml")
    build(RecallsBuildConfig.from_yaml(ap.parse_args().config))


if __name__ == "__main__":
    main()
