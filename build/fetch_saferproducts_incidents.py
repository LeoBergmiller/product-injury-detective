"""Pull all SaferProducts IncidentDetails (plus small lookup sets) to data/raw/.

Paging: the server caps IncidentDetails at 50 rows per page and returns `__next` with a
`$skiptoken`, but `__next` points at an internal host. We take only the `$skiptoken`
value and rebuild the URL on the public base URL. The run is resumable through
`checkpoint.json`.

Auth: the key is the basic-auth username with a blank password, read from .env. It is
never logged and never placed in a URL.

Usage:
  uv run python build/fetch_saferproducts_incidents.py --test-page   # page 1 only
  uv run python build/fetch_saferproducts_incidents.py               # full / resume
"""

from __future__ import annotations

import argparse
import json
import logging
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from fetch_saferproducts import (
    REPO_ROOT,
    SaferProductsConfig,
    basic_auth_header,
    load_key,
)
from netutil import FetchError, Fetcher

LOG = logging.getLogger("fetch_incidents")


@dataclass(frozen=True)
class IncidentsConfig:
    out_dir: Path
    min_interval_s: float
    retries: int
    backoff_s: float
    timeout_s: float
    entity_set: str
    select: list[str]
    lookups: dict[str, list[str]]
    lookup_page_size: int

    @classmethod
    def from_yaml(cls, path: Path) -> IncidentsConfig:
        raw = yaml.safe_load(path.read_text())["saferproducts_incidents"]
        return cls(
            out_dir=REPO_ROOT / raw["out_dir"],
            min_interval_s=float(raw["min_interval_s"]),
            retries=int(raw["retries"]),
            backoff_s=float(raw["backoff_s"]),
            timeout_s=float(raw["timeout_s"]),
            entity_set=raw["entity_set"],
            select=list(raw["select"]),
            lookups={k: list(v) for k, v in raw["lookups"].items()},
            lookup_page_size=int(raw["lookup_page_size"]),
        )


def unwrap(payload: dict[str, Any]) -> tuple[list[dict], str | None]:
    """Return (rows, __next) for OData v1 (bare array) or v2 ({results, __next})."""
    d = payload.get("d", payload.get("value"))
    if isinstance(d, dict):
        return d.get("results", []), d.get("__next")
    return d, None


def skiptoken_from_next(next_url: str) -> str:
    """Extract $skiptoken from a __next URL. The URL's host is ignored (internal)."""
    qs = urllib.parse.parse_qs(urllib.parse.urlsplit(next_url).query)
    tokens = qs.get("$skiptoken")
    if not tokens:
        raise FetchError(f"__next has no $skiptoken: {next_url[:200]}")
    return tokens[0]


def page_url(base: str, cfg: IncidentsConfig, skiptoken: str | None) -> str:
    params: dict[str, str] = {"$select": ",".join(cfg.select)}
    if skiptoken is not None:
        params["$skiptoken"] = skiptoken
    return f"{base}{cfg.entity_set}?{urllib.parse.urlencode(params)}"


def write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def fetch_lookups(f: Fetcher, base: str, cfg: IncidentsConfig) -> None:
    lk_dir = cfg.out_dir / "lookups"
    lk_dir.mkdir(parents=True, exist_ok=True)
    for name, fields in cfg.lookups.items():
        if any(lk_dir.glob(f"{name}_p*.json")):
            LOG.info("Lookup %s already saved; skipping", name)
            continue
        page = 0
        while True:
            params = {
                "$select": ",".join(fields),
                "$top": cfg.lookup_page_size,
                "$skip": page * cfg.lookup_page_size,
            }
            url = f"{base}{name}?{urllib.parse.urlencode(params)}"
            body = f.get(url, f"{name}_p{page}")
            rows, nxt = unwrap(json.loads(body))
            write_atomic(lk_dir / f"{name}_p{page}.json", body)
            LOG.info("Lookup %s page %d: %d rows", name, page, len(rows))
            if len(rows) < cfg.lookup_page_size and not nxt:
                break
            page += 1


def run(f: Fetcher, base: str, cfg: IncidentsConfig, test_page: bool) -> None:
    pages_dir = cfg.out_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    ckpt_path = cfg.out_dir / "checkpoint.json"
    ckpt: dict[str, Any] = {"page": 0, "rows": 0, "skiptoken": None, "done": False}
    if ckpt_path.exists() and not test_page:
        ckpt = json.loads(ckpt_path.read_text())
        LOG.info("Resuming from checkpoint: %s", ckpt)
    if ckpt.get("done"):
        LOG.info("Checkpoint says done; nothing to fetch")
        return

    while True:
        page_no = ckpt["page"] + 1
        url = page_url(base, cfg, ckpt["skiptoken"])
        body = f.get(url, f"incidents_page_{page_no}")
        rows, nxt = unwrap(json.loads(body))
        if not rows and nxt:
            raise FetchError(f"page {page_no}: 0 rows but __next present")
        write_atomic(pages_dir / f"page_{page_no:05d}.json", body)
        last_id = rows[-1]["IncidentReportId"] if rows else None

        if test_page:
            LOG.info(
                "TEST page 1: %d rows, ids %s..%s, __next host=%s",
                len(rows),
                rows[0]["IncidentReportId"] if rows else None,
                last_id,
                urllib.parse.urlsplit(nxt).netloc if nxt else None,
            )
            if nxt:
                tok = skiptoken_from_next(nxt)
                LOG.info(
                    "TEST: $skiptoken=%s; equals last id: %s", tok, tok == str(last_id)
                )
                body2 = f.get(page_url(base, cfg, tok), "incidents_test_page_2")
                rows2, _ = unwrap(json.loads(body2))
                LOG.info(
                    "TEST page 2 via public host: %d rows, first id %s (> %s: %s)",
                    len(rows2),
                    rows2[0]["IncidentReportId"] if rows2 else None,
                    last_id,
                    bool(rows2) and rows2[0]["IncidentReportId"] > last_id,
                )
            return

        ckpt["page"] = page_no
        ckpt["rows"] += len(rows)
        if not nxt:
            ckpt["done"] = True
            write_atomic(ckpt_path, json.dumps(ckpt).encode())
            LOG.info("Done: %d pages, %d rows", ckpt["page"], ckpt["rows"])
            return
        tok = skiptoken_from_next(nxt)
        if ckpt["skiptoken"] is not None and int(tok) <= int(ckpt["skiptoken"]):
            raise FetchError(f"skiptoken did not advance: {ckpt['skiptoken']} -> {tok}")
        ckpt["skiptoken"] = tok
        write_atomic(ckpt_path, json.dumps(ckpt).encode())
        if page_no % 50 == 0:
            LOG.info(
                "Progress: page %d, %d rows, last id %s, requests %d",
                page_no,
                ckpt["rows"],
                last_id,
                f.n_requests,
            )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", type=Path, default=REPO_ROOT / "build/config.yaml")
    ap.add_argument("--test-page", action="store_true", help="fetch page 1 (+2) only")
    ap.add_argument("--skip-lookups", action="store_true")
    args = ap.parse_args()

    sp = SaferProductsConfig.from_yaml(args.config)
    cfg = IncidentsConfig.from_yaml(args.config)
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(cfg.out_dir / "fetch.log"),
        ],
    )
    key = load_key(sp)
    f = Fetcher(
        min_interval_s=cfg.min_interval_s,
        retries=cfg.retries,
        backoff_s=cfg.backoff_s,
        timeout_s=cfg.timeout_s,
        auth_header=basic_auth_header(key),
        secret=key.encode(),
    )
    try:
        if not args.test_page and not args.skip_lookups:
            fetch_lookups(f, sp.base_url, cfg)
        run(f, sp.base_url, cfg, args.test_page)
    except FetchError as err:
        LOG.error("STOPPED: %s (requests so far: %d)", err, f.n_requests)
        raise SystemExit(2) from err
    LOG.info("Requests made: %d", f.n_requests)


if __name__ == "__main__":
    main()
