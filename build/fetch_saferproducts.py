"""Probe the SaferProducts.gov incident-report OData API.

Auth: the API key is sent as the HTTP basic-auth username with a blank password.
The key is read from the environment or .env and is never printed or logged.

Steps:
1. GET $metadata; save the XML; log entity sets and their properties.
2. GET a small sample ($top=N) from one entity set; save it as JSON.
3. Send a short burst of sequential small requests; log status codes, latency, and any
   rate-limit headers.

Usage: uv run python build/fetch_saferproducts.py [--config build/config.yaml]
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

LOG = logging.getLogger("fetch_saferproducts")
REPO_ROOT = Path(__file__).resolve().parent.parent
RATE_HEADER_HINTS = ("ratelimit", "rate-limit", "retry-after", "x-ms-", "throttl")


@dataclass(frozen=True)
class SaferProductsConfig:
    base_url: str
    key_env_var: str
    env_file: Path
    out_dir: Path
    timeout_s: float
    entity_set: str | None
    sample_top: int
    accept_headers: list[str]
    rate_probe_requests: int
    rate_probe_delay_s: float

    @classmethod
    def from_yaml(cls, path: Path) -> SaferProductsConfig:
        raw = yaml.safe_load(path.read_text())["saferproducts"]
        return cls(
            base_url=raw["base_url"].rstrip("/") + "/",
            key_env_var=raw["key_env_var"],
            env_file=REPO_ROOT / raw["env_file"],
            out_dir=REPO_ROOT / raw["out_dir"],
            timeout_s=float(raw["timeout_s"]),
            entity_set=raw.get("entity_set"),
            sample_top=int(raw["sample_top"]),
            accept_headers=list(raw["accept_headers"]),
            rate_probe_requests=int(raw["rate_probe_requests"]),
            rate_probe_delay_s=float(raw["rate_probe_delay_s"]),
        )


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes
    elapsed_s: float


def load_key(cfg: SaferProductsConfig) -> str:
    """Return the API key from the environment or .env. Never log the value."""
    key = os.environ.get(cfg.key_env_var, "").strip()
    if not key and cfg.env_file.exists():
        for line in cfg.env_file.read_text().splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == cfg.key_env_var:
                key = value.strip().strip("'\"")
                break
    if not key:
        raise SystemExit(f"{cfg.key_env_var} not set in environment or {cfg.env_file}")
    LOG.info("Loaded %s (length %d; value not logged)", cfg.key_env_var, len(key))
    return key


def basic_auth_header(key: str) -> str:
    """Basic auth with the key as username and a blank password."""
    return "Basic " + base64.b64encode(f"{key}:".encode()).decode()


def get(url: str, auth: str, accept: str, timeout_s: float) -> Response:
    """GET a URL. Return status, headers, and body even for HTTP error codes."""
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": auth,
            "Accept": accept,
            "User-Agent": "product-injury-detective/0.1",
        },
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read()
            return Response(
                resp.status, dict(resp.headers), body, time.perf_counter() - start
            )
    except urllib.error.HTTPError as err:
        return Response(
            err.code, dict(err.headers), err.read(), time.perf_counter() - start
        )


def rate_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        k: v
        for k, v in headers.items()
        if any(hint in k.lower() for hint in RATE_HEADER_HINTS)
    }


def parse_metadata(xml_bytes: bytes) -> dict[str, Any]:
    """Extract entity sets and entity-type properties from OData CSDL (any version)."""
    root = ET.fromstring(xml_bytes)
    types: dict[str, list[dict[str, str]]] = {}
    sets: dict[str, str] = {}
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag == "EntityType":
            types[el.get("Name", "")] = [
                {"name": p.get("Name", ""), "type": p.get("Type", "")}
                for p in el
                if p.tag.split("}")[-1] == "Property"
            ]
        elif tag == "EntitySet":
            sets[el.get("Name", "")] = el.get("EntityType", "").split(".")[-1]
    return {"entity_sets": sets, "entity_types": types}


def fetch_metadata(cfg: SaferProductsConfig, auth: str) -> dict[str, Any]:
    url = cfg.base_url + "$metadata"
    resp = get(url, auth, "application/xml", cfg.timeout_s)
    LOG.info(
        "$metadata -> HTTP %d, %d bytes, %.2fs, content-type=%s",
        resp.status,
        len(resp.body),
        resp.elapsed_s,
        resp.headers.get("Content-Type"),
    )
    if resp.status != 200:
        LOG.error("Body (first 500 bytes): %r", resp.body[:500])
        raise SystemExit(f"$metadata failed with HTTP {resp.status}")
    (cfg.out_dir / "metadata.xml").write_bytes(resp.body)
    meta = parse_metadata(resp.body)
    for set_name, type_name in meta["entity_sets"].items():
        props = meta["entity_types"].get(type_name, [])
        LOG.info("EntitySet %s (%s): %d properties", set_name, type_name, len(props))
        for p in props:
            LOG.info("    %s: %s", p["name"], p["type"])
    return meta


def fetch_sample(
    cfg: SaferProductsConfig, auth: str, entity_set: str
) -> tuple[str, Response]:
    query = urllib.parse.urlencode({"$top": cfg.sample_top})
    url = f"{cfg.base_url}{entity_set}?{query}"
    resp: Response | None = None
    for accept in cfg.accept_headers:
        resp = get(url, auth, accept, cfg.timeout_s)
        LOG.info(
            "Sample %s (Accept: %s) -> HTTP %d, %d bytes, %.2fs, content-type=%s",
            entity_set,
            accept,
            resp.status,
            len(resp.body),
            resp.elapsed_s,
            resp.headers.get("Content-Type"),
        )
        if resp.status == 200:
            out = cfg.out_dir / f"sample_{entity_set}.json"
            out.write_bytes(resp.body)
            LOG.info("Saved sample to %s", out.relative_to(REPO_ROOT))
            return accept, resp
    assert resp is not None
    LOG.error("Sample failed. Body (first 500 bytes): %r", resp.body[:500])
    raise SystemExit(f"Sample request for {entity_set} failed")


def log_sample_fields(body: bytes) -> list[str]:
    """Log the field names of the first record in an OData JSON response."""
    data = json.loads(body)
    records = data.get("value") or data.get("d", {})
    if isinstance(records, dict):  # OData verbose: {"d": {"results": [...]}}
        records = records.get("results", [])
    if not records:
        LOG.warning("Sample returned 0 records; top-level keys: %s", list(data))
        return []
    fields = list(records[0].keys())
    LOG.info("Sample returned %d records; fields: %s", len(records), fields)
    return fields


def rate_probe(
    cfg: SaferProductsConfig, auth: str, entity_set: str, accept: str
) -> dict[str, Any]:
    url = f"{cfg.base_url}{entity_set}?" + urllib.parse.urlencode({"$top": 1})
    statuses: dict[int, int] = {}
    latencies: list[float] = []
    seen_headers: dict[str, str] = {}
    start = time.perf_counter()
    for _ in range(cfg.rate_probe_requests):
        resp = get(url, auth, accept, cfg.timeout_s)
        statuses[resp.status] = statuses.get(resp.status, 0) + 1
        latencies.append(resp.elapsed_s)
        seen_headers.update(rate_headers(resp.headers))
        if resp.status == 429:
            LOG.warning(
                "HTTP 429 received; Retry-After=%s", resp.headers.get("Retry-After")
            )
            break
        time.sleep(cfg.rate_probe_delay_s)
    total = time.perf_counter() - start
    summary = {
        "requests": sum(statuses.values()),
        "status_counts": statuses,
        "wall_s": round(total, 2),
        "achieved_req_per_s": round(sum(statuses.values()) / total, 2),
        "latency_s_min": round(min(latencies), 3),
        "latency_s_max": round(max(latencies), 3),
        "latency_s_mean": round(sum(latencies) / len(latencies), 3),
        "rate_limit_headers": seen_headers,
    }
    LOG.info("Rate probe: %s", summary)
    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "build/config.yaml")
    args = parser.parse_args()

    cfg = SaferProductsConfig.from_yaml(args.config)
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    auth = basic_auth_header(load_key(cfg))

    meta = fetch_metadata(cfg, auth)
    sets = list(meta["entity_sets"])
    if not sets:
        raise SystemExit("No entity sets found in $metadata")
    entity_set = cfg.entity_set or sets[0]
    accept, sample = fetch_sample(cfg, auth, entity_set)
    fields = log_sample_fields(sample.body)
    probe = rate_probe(cfg, auth, entity_set, accept)

    summary = {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "base_url": cfg.base_url,
        "entity_sets": meta["entity_sets"],
        "sampled_entity_set": entity_set,
        "sample_accept_header": accept,
        "sample_fields": fields,
        "sample_response_headers": {
            k: v for k, v in sample.headers.items() if k.lower() != "set-cookie"
        },
        "rate_probe": probe,
    }
    out = cfg.out_dir / "probe_summary.json"
    out.write_text(json.dumps(summary, indent=2))
    LOG.info("Wrote %s", out.relative_to(REPO_ROOT))


if __name__ == "__main__":
    main()
