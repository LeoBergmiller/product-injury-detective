"""Extract the NEISS product-code -> title list from the coding manual (Appendix H).

Appendix H ("Numerical Listing of NEISS Product and Activity Codes") lists one
"NNNN Title" entry per line, under a "Code Product Title" header on each page.
Only pages with that header are parsed. The appendix has no category headings.

Output: data/build/product_codes.parquet (see docs/SCHEMA.md once written).
Also logs coverage against the NEISS codes actually used in the configured files.

Usage: uv run python build/build_product_codes.py [--config build/config.yaml]
Requires the dev dependency pypdf (build-time only).
"""

from __future__ import annotations

import argparse
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import duckdb
import yaml
from pypdf import PdfReader

LOG = logging.getLogger("build_product_codes")
REPO_ROOT = Path(__file__).resolve().parent.parent
# Code, then one or more spaces (0478 uses two), then the title.
ENTRY_RE = re.compile(r"^(\d{4})\s+(\S.*?)\s*$")
WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class ProductCodesConfig:
    manual_pdf: Path
    manual_label: str
    page_header: str
    out_parquet: Path
    coverage_tsvs: list[Path]

    @classmethod
    def from_yaml(cls, path: Path) -> ProductCodesConfig:
        raw = yaml.safe_load(path.read_text())["product_codes"]
        return cls(
            manual_pdf=REPO_ROOT / raw["manual_pdf"],
            manual_label=raw["manual_label"],
            page_header=raw["page_header"],
            out_parquet=REPO_ROOT / raw["out_parquet"],
            coverage_tsvs=[REPO_ROOT / p for p in raw["coverage_tsvs"]],
        )


@dataclass(frozen=True)
class Entry:
    product_code: int
    product_title: str
    source_page: int


def parse_manual(cfg: ProductCodesConfig) -> list[Entry]:
    reader = PdfReader(cfg.manual_pdf)
    entries: list[Entry] = []
    pages_used: list[int] = []
    for page_no, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if cfg.page_header not in text:
            continue
        pages_used.append(page_no)
        for line in text.splitlines():
            m = ENTRY_RE.match(line)
            if m:
                title = WS_RE.sub(" ", m.group(2))
                entries.append(Entry(int(m.group(1)), title, page_no))
    LOG.info(
        "Parsed %d entries from %d pages (%s-%s) of %s",
        len(entries),
        len(pages_used),
        pages_used[0] if pages_used else None,
        pages_used[-1] if pages_used else None,
        cfg.manual_pdf.name,
    )
    return entries


def validate(entries: list[Entry]) -> None:
    codes = [e.product_code for e in entries]
    dupes = sorted({c for c in codes if codes.count(c) > 1})
    if dupes:
        raise SystemExit(f"Duplicate product codes in manual parse: {dupes}")
    out_of_order = [
        (a.product_code, b.product_code)
        for a, b in zip(entries, entries[1:])
        if b.product_code <= a.product_code
    ]
    if out_of_order:
        raise SystemExit(f"Codes not ascending (parse problem?): {out_of_order[:10]}")
    empty = [e.product_code for e in entries if not e.product_title.strip()]
    if empty:
        raise SystemExit(f"Empty titles for codes: {empty}")
    LOG.info("Validation OK: unique, ascending, non-empty titles")


def write_parquet(cfg: ProductCodesConfig, entries: list[Entry]) -> None:
    cfg.out_parquet.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(
        """CREATE TABLE product_codes (
            product_code INTEGER PRIMARY KEY,
            product_title VARCHAR NOT NULL,
            source_page INTEGER NOT NULL,
            source VARCHAR NOT NULL)"""
    )
    con.executemany(
        "INSERT INTO product_codes VALUES (?, ?, ?, ?)",
        [
            (e.product_code, e.product_title, e.source_page, cfg.manual_label)
            for e in entries
        ],
    )
    con.execute(f"COPY product_codes TO '{cfg.out_parquet}' (FORMAT parquet)")
    LOG.info("Wrote %s", cfg.out_parquet.relative_to(REPO_ROOT))


def coverage(cfg: ProductCodesConfig) -> None:
    """Log how many product codes used in the NEISS files have a manual title."""
    con = duckdb.connect()
    for tsv in cfg.coverage_tsvs:
        rows = con.sql(
            f"""
            WITH r AS (SELECT * FROM read_csv('{tsv}', delim='\t', header=true,
                                              all_varchar=true)),
            u AS (SELECT CAST(Product_1 AS INT) p, 1 slot,
                         CAST(Weight AS DOUBLE) w FROM r
                  UNION ALL SELECT CAST(Product_2 AS INT), 2, CAST(Weight AS DOUBLE)
                  FROM r
                  UNION ALL SELECT CAST(Product_3 AS INT), 3, CAST(Weight AS DOUBLE)
                  FROM r),
            pc AS (SELECT * FROM read_parquet('{cfg.out_parquet}'))
            SELECT
              count(DISTINCT p) FILTER (WHERE slot = 1),
              count(DISTINCT p) FILTER (WHERE slot = 1 AND pc.product_code IS NULL),
              count(DISTINCT p),
              count(DISTINCT p) FILTER (WHERE pc.product_code IS NULL),
              count(*) FILTER (WHERE slot = 1 AND pc.product_code IS NULL),
              list(DISTINCT p) FILTER (WHERE pc.product_code IS NULL)
            FROM u LEFT JOIN pc ON u.p = pc.product_code
            WHERE u.p <> 0
            """
        ).fetchall()[0]
        p1, p1_missing, anyslot, any_missing, p1_rows_missing, missing = rows
        LOG.info(
            "%s: Product_1 distinct=%d, without title=%d (rows=%d); "
            "any slot distinct=%d, without title=%d; missing codes=%s",
            tsv.name,
            p1,
            p1_missing,
            p1_rows_missing,
            anyslot,
            any_missing,
            sorted(missing or []),
        )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", type=Path, default=REPO_ROOT / "build/config.yaml")
    cfg = ProductCodesConfig.from_yaml(ap.parse_args().config)
    entries = parse_manual(cfg)
    validate(entries)
    write_parquet(cfg, entries)
    coverage(cfg)


if __name__ == "__main__":
    main()
