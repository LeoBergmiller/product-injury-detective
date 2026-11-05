"""Measure SaferProducts IncidentDetails size and paging behaviour (3 requests).

1. $inlinecount=allpages&$top=1 -> total count (if the server supports it).
2. A large $top page with a narrow $select -> server page-size cap and __next.
3. $skip near the end -> deep-skip behaviour and the newest records.

Same safety rules as probe_join_key.py: stop on any non-200, any auth challenge, or the
key appearing in a body. Raw responses go to data/raw/saferproducts/size_probe/.

Usage: uv run python build/probe_size.py [--config build/config.yaml]
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fetch_saferproducts import REPO_ROOT, SaferProductsConfig, load_key
from probe_join_key import Client, ProbeConfig, StopProbe

LOG = logging.getLogger("probe_size")
DATE_RE = re.compile(r"/Date\((-?\d+)\)/")
SELECT = "IncidentReportId,IncidentReportDate"
BIG_TOP = 5000


def wcf_date(s: str | None) -> str | None:
    m = DATE_RE.match(s or "")
    if not m:
        return None
    ts = datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc)
    return ts.date().isoformat()


def unwrap(payload: Any) -> tuple[list[dict], dict[str, Any]]:
    """Return (rows, extras) for OData v1 array or v2 {results, __count, __next}."""
    d = payload.get("d", payload.get("value"))
    if isinstance(d, dict):
        return d.get("results", []), {k: v for k, v in d.items() if k != "results"}
    return d, {}


def summarize(rows: list[dict]) -> dict[str, Any]:
    ids = [r["IncidentReportId"] for r in rows]
    dates = [wcf_date(r.get("IncidentReportDate")) for r in rows]
    dates = [x for x in dates if x]
    return {
        "rows": len(rows),
        "id_min": min(ids) if ids else None,
        "id_max": max(ids) if ids else None,
        "id_ascending": ids == sorted(ids),
        "report_date_min": min(dates) if dates else None,
        "report_date_max": max(dates) if dates else None,
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", type=Path, default=REPO_ROOT / "build/config.yaml")
    args = ap.parse_args()
    sp = SaferProductsConfig.from_yaml(args.config)
    pc = ProbeConfig.from_yaml(args.config)
    pc = dataclasses.replace(pc, out_dir=pc.out_dir.parent / "size_probe")
    pc.out_dir.mkdir(parents=True, exist_ok=True)
    c = Client(sp, pc, load_key(sp))
    out: dict[str, Any] = {}
    try:
        p1 = c.odata_raw(
            "count_inlinecount",
            "IncidentDetails",
            {"$inlinecount": "allpages", "$top": 1, "$select": SELECT},
        )
        rows1, extra1 = unwrap(p1)
        total = int(extra1["__count"]) if "__count" in extra1 else None
        out["inlinecount"] = {
            "total": total,
            "extras": list(extra1),
            **summarize(rows1),
        }
        LOG.info("Total via $inlinecount: %s", total)

        p2 = c.odata_raw(
            f"page_top{BIG_TOP}",
            "IncidentDetails",
            {"$top": BIG_TOP, "$select": SELECT},
        )
        rows2, extra2 = unwrap(p2)
        out["big_page"] = {
            "requested_top": BIG_TOP,
            "has___next": "__next" in extra2,
            "__next": extra2.get("__next"),
            **summarize(rows2),
        }
        LOG.info("Big page: %s", out["big_page"])

        if total:
            skip = max(total - 3, 0)
            p3 = c.odata_raw(
                "tail_skip",
                "IncidentDetails",
                {"$skip": skip, "$top": 3, "$select": SELECT},
            )
            rows3, _ = unwrap(p3)
            out["tail"] = {"skip": skip, **summarize(rows3)}
            LOG.info("Tail: %s", out["tail"])
    except StopProbe as err:
        LOG.error("STOPPED: %s", err)
        (pc.out_dir / "size_probe_findings.json").write_text(json.dumps(out, indent=2))
        raise SystemExit(2) from err
    out["requests_made"] = c.n_requests
    path = pc.out_dir / "size_probe_findings.json"
    path.write_text(json.dumps(out, indent=2))
    LOG.info("Wrote %s", path.relative_to(REPO_ROOT))


if __name__ == "__main__":
    main()
