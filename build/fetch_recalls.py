"""Snapshot the CPSC Recalls API into data/raw/recalls/snapshot/, one file per year.

The API returns every match in one JSON array (no paging). A malformed request returns
HTTP 200 with an error pseudo-recall (`RecallID == 0`, Title "Error retrieving ..."),
so each response is validated and a pseudo-recall counts as a failed attempt.

Public API: no Authorization header is sent. <= 2 req/s.

Usage: uv run python build/fetch_recalls.py [--refresh]
"""

from __future__ import annotations

import argparse
import json
import logging
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import yaml
from netutil import FetchError, Fetcher

LOG = logging.getLogger("fetch_recalls")
REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class RecallsConfig:
    base_url: str
    snapshot_dir: Path
    start_year: int
    end_year: int
    min_interval_s: float
    retries: int
    backoff_s: float
    timeout_s: float

    @classmethod
    def from_yaml(cls, path: Path) -> RecallsConfig:
        raw = yaml.safe_load(path.read_text())["recalls"]
        return cls(
            base_url=raw["base_url"],
            snapshot_dir=REPO_ROOT / raw["snapshot_dir"],
            start_year=int(raw["start_year"]),
            end_year=int(raw["end_year"]),
            min_interval_s=float(raw["min_interval_s"]),
            retries=int(raw["retries"]),
            backoff_s=float(raw["backoff_s"]),
            timeout_s=float(raw["timeout_s"]),
        )


def is_pseudo_error(data: list[dict]) -> bool:
    return any(
        r.get("RecallID") == 0
        or str(r.get("Title") or "").startswith("Error retrieving")
        for r in data
    )


def fetch_year(f: Fetcher, cfg: RecallsConfig, year: int) -> list[dict]:
    params = {
        "format": "json",
        "RecallDateStart": f"{year}-01-01",
        "RecallDateEnd": f"{year}-12-31",
    }
    url = f"{cfg.base_url}?{urllib.parse.urlencode(params)}"
    for attempt in range(cfg.retries + 1):
        body = f.get(url, f"recalls_{year}")
        data = json.loads(body)
        if not isinstance(data, list):
            raise FetchError(f"recalls_{year}: expected a JSON array")
        if not is_pseudo_error(data):
            out = cfg.snapshot_dir / f"recalls_{year}.json"
            tmp = out.with_suffix(".json.tmp")
            tmp.write_bytes(body)
            tmp.replace(out)
            return data
        if attempt < cfg.retries:
            delay = cfg.backoff_s * (2**attempt)
            LOG.warning("recalls_%d: error pseudo-recall; retry in %.1fs", year, delay)
            time.sleep(delay)
    raise FetchError(f"recalls_{year}: error pseudo-recall after {cfg.retries} retries")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", type=Path, default=REPO_ROOT / "build/config.yaml")
    ap.add_argument("--refresh", action="store_true", help="refetch saved years")
    args = ap.parse_args()
    cfg = RecallsConfig.from_yaml(args.config)
    cfg.snapshot_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(cfg.snapshot_dir / "fetch.log"),
        ],
    )
    f = Fetcher(
        min_interval_s=cfg.min_interval_s,
        retries=cfg.retries,
        backoff_s=cfg.backoff_s,
        timeout_s=cfg.timeout_s,
    )
    counts: dict[int, int] = {}
    try:
        for year in range(cfg.start_year, cfg.end_year + 1):
            out = cfg.snapshot_dir / f"recalls_{year}.json"
            if out.exists() and not args.refresh:
                counts[year] = len(json.loads(out.read_bytes()))
                continue
            counts[year] = len(fetch_year(f, cfg, year))
            LOG.info("%d: %d recalls", year, counts[year])
    except FetchError as err:
        LOG.error("STOPPED: %s", err)
        raise SystemExit(2) from err
    (cfg.snapshot_dir / "counts.json").write_text(json.dumps(counts, indent=1))
    LOG.info("Total %d recalls over %d years; %d requests", sum(counts.values()),
             len(counts), f.n_requests)


if __name__ == "__main__":
    main()
