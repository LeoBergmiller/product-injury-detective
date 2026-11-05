"""Probe whether SaferProducts, the Recalls API, and NEISS share a product-code scheme.

Read-only GETs. The key is sent only to SaferProducts, as the basic-auth username, and
is never logged. The script stops on any non-200 response, on any auth challenge, or
if a response body contains the key. Raw responses are saved under data/raw/.

Usage: uv run python build/probe_join_key.py [--config build/config.yaml]
"""

from __future__ import annotations

import argparse
import json
import logging
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import yaml
from fetch_saferproducts import (
    REPO_ROOT,
    Response,
    SaferProductsConfig,
    basic_auth_header,
    get,
    load_key,
)

LOG = logging.getLogger("probe_join_key")


@dataclass(frozen=True)
class ProbeConfig:
    out_dir: Path
    recalls_out_dir: Path
    recalls_base_url: str
    recalls_timeout_s: float
    delay_s: float
    max_lookup_pages: int
    lookup_page_size: int
    sample_top: int
    incident_filter_code: int
    recall_windows: list[tuple[str, str]]
    neiss_tsv: Path

    @classmethod
    def from_yaml(cls, path: Path) -> ProbeConfig:
        raw = yaml.safe_load(path.read_text())
        jp, rc = raw["join_probe"], raw["recalls"]
        return cls(
            out_dir=REPO_ROOT / jp["out_dir"],
            recalls_out_dir=REPO_ROOT / rc["out_dir"],
            recalls_base_url=rc["base_url"],
            recalls_timeout_s=float(rc["timeout_s"]),
            delay_s=float(jp["delay_s"]),
            max_lookup_pages=int(jp["max_lookup_pages"]),
            lookup_page_size=int(jp["lookup_page_size"]),
            sample_top=int(jp["sample_top"]),
            incident_filter_code=int(jp["incident_filter_code"]),
            recall_windows=[tuple(w) for w in jp["recall_windows"]],
            neiss_tsv=REPO_ROOT / jp["neiss_tsv"],
        )


class StopProbe(RuntimeError):
    """Raised when a safety condition trips; the probe halts immediately."""

    def __init__(self, msg: str, status: int | None = None) -> None:
        super().__init__(msg)
        self.status = status


class Client:
    def __init__(self, sp: SaferProductsConfig, pc: ProbeConfig, key: str) -> None:
        self.sp, self.pc = sp, pc
        self._key_bytes = key.encode()
        self._auth = basic_auth_header(key)
        self.n_requests = 0

    def _check(self, label: str, resp: Response) -> None:
        self.n_requests += 1
        LOG.info(
            "[%02d] %s -> HTTP %d, %d bytes, %.2fs",
            self.n_requests,
            label,
            resp.status,
            len(resp.body),
            resp.elapsed_s,
        )
        if any(k.lower() == "www-authenticate" for k in resp.headers):
            raise StopProbe(f"{label}: unexpected auth challenge (WWW-Authenticate)")
        if resp.status != 200:
            err_path = self.pc.out_dir / f"{label}.HTTP{resp.status}.body"
            err_path.write_bytes(resp.body)
            raise StopProbe(
                f"{label}: HTTP {resp.status}; body saved to "
                f"{err_path.relative_to(REPO_ROOT)}",
                status=resp.status,
            )
        if self._key_bytes in resp.body:
            raise StopProbe(f"{label}: response body contains the API key")
        time.sleep(self.pc.delay_s)

    def odata_raw(self, label: str, path: str, params: dict[str, Any]) -> Any:
        """GET an OData resource; save and return the parsed JSON payload as-is."""
        url = self.sp.base_url + path + "?" + urllib.parse.urlencode(params)
        resp = get(url, self._auth, "application/json", self.sp.timeout_s)
        self._check(label, resp)
        (self.pc.out_dir / f"{label}.json").write_bytes(resp.body)
        return json.loads(resp.body)

    def odata(self, label: str, path: str, params: dict[str, Any]) -> list[dict]:
        payload = self.odata_raw(label, path, params)
        data = payload.get("d", payload.get("value"))
        if isinstance(data, dict):
            if "__next" in data:
                LOG.info("    %s: __next present (server-side paging)", label)
            data = data.get("results", [])
        return data

    def recalls(self, label: str, start: str, end: str) -> list[dict]:
        params = {"format": "json", "RecallDateStart": start, "RecallDateEnd": end}
        url = self.pc.recalls_base_url + "?" + urllib.parse.urlencode(params)
        # Public API: no Authorization header is sent.
        resp = get(url, "", "application/json", self.pc.recalls_timeout_s)
        self._check(label, resp)
        (self.pc.recalls_out_dir / f"{label}.json").write_bytes(resp.body)
        data = json.loads(resp.body)
        if len(data) == 1 and data[0].get("RecallID") == 0:
            raise StopProbe(f"{label}: Recalls API error pseudo-recall returned")
        return data


def fetch_product_categories(c: Client) -> list[dict]:
    rows: list[dict] = []
    for page in range(c.pc.max_lookup_pages):
        batch = c.odata(
            f"ProductCategories_p{page}",
            "ProductCategories",
            {"$top": c.pc.lookup_page_size, "$skip": page * c.pc.lookup_page_size},
        )
        rows.extend(batch)
        LOG.info("    ProductCategories page %d: %d rows", page, len(batch))
        if len(batch) < c.pc.lookup_page_size:
            break
    return rows


def load_saved_categories(out_dir: Path) -> list[dict]:
    """Reuse ProductCategories pages saved by an earlier run (no network)."""
    rows: list[dict] = []
    for p in sorted(out_dir.glob("ProductCategories_p*.json")):
        rows += json.loads(p.read_bytes())["d"]
    return rows


def or_filter(field: str, ids: list[int]) -> str:
    return " or ".join(f"{field} eq {i}" for i in ids)


def neiss_codes(tsv: Path) -> dict[int, dict[str, Any]]:
    """Distinct NEISS 2025 product codes (any of Product_1/2/3) with row counts."""
    sql = f"""
        WITH r AS (SELECT * FROM read_csv('{tsv}', delim='\t', header=true,
                                          all_varchar=true)),
        u AS (SELECT CAST(Product_1 AS INT) p, 1 AS slot FROM r
              UNION ALL SELECT CAST(Product_2 AS INT), 2 FROM r
              UNION ALL SELECT CAST(Product_3 AS INT), 3 FROM r)
        SELECT p, count(*) n, count(*) FILTER (WHERE slot = 1) n_p1
        FROM u WHERE p <> 0 GROUP BY p
    """
    return {p: {"n": n, "n_p1": n1} for p, n, n1 in duckdb.sql(sql).fetchall()}


def neiss_narratives(tsv: Path, code: int, k: int = 3) -> list[str]:
    sql = f"""SELECT Narrative_1 FROM read_csv('{tsv}', delim='\t', header=true,
              all_varchar=true) WHERE Product_1 = '{code}'
              ORDER BY CPSC_Case_Number LIMIT {k}"""
    return [r[0][:110] for r in duckdb.sql(sql).fetchall()]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", type=Path, default=REPO_ROOT / "build/config.yaml")
    args = ap.parse_args()
    sp = SaferProductsConfig.from_yaml(args.config)
    pc = ProbeConfig.from_yaml(args.config)
    pc.out_dir.mkdir(parents=True, exist_ok=True)
    pc.recalls_out_dir.mkdir(parents=True, exist_ok=True)
    c = Client(sp, pc, load_key(sp))
    findings: dict[str, Any] = {}

    rd: list[dict] = []
    rpc: list[dict] = []
    rhd: list[dict] = []
    sp_rd: list[dict] = []
    sp_rpc: list[dict] = []
    findings["sp_recall_sets_status"] = "not attempted"
    try:
        # (a) SaferProducts ProductCategories lookup: reuse saved pages if present
        cats = load_saved_categories(pc.out_dir)
        if cats:
            LOG.info("Reusing %d saved ProductCategories rows (no request)", len(cats))
        else:
            cats = fetch_product_categories(c)
        cat_ids = {r["ProductCategoryId"]: r for r in cats}
        findings["product_categories_n"] = len(cats)

        # (b) Recalls API CategoryID for real recalls (public, no auth) -- first
        api_recalls: list[dict] = []
        for start, end in pc.recall_windows:
            api_recalls += c.recalls(f"recalls_{start}_{end}", start, end)
        api_ids = sorted({r["RecallID"] for r in api_recalls})[:40]

        # SaferProducts recall sets. A 503 here skips all of them (no retry);
        # any other failure still stops the whole probe.
        try:
            rd = c.odata(
                "RecallDetails_top", "RecallDetails", {"$top": pc.sample_top}
            )
            findings["sp_recall_sets_status"] = "available"
        except StopProbe as err:
            if err.status != 503:
                raise
            LOG.warning("RecallDetails 503 again: skipping SP recall sets (%s)", err)
            findings["sp_recall_sets_status"] = "HTTP 503 on RecallDetails; skipped"

        # One filtered incident sample, to test $filter on ProductCategoryId
        inc = c.odata(
            f"IncidentDetails_cat{pc.incident_filter_code}",
            "IncidentDetails",
            {
                "$filter": f"ProductCategoryId eq {pc.incident_filter_code}",
                "$top": 5,
                "$select": "IncidentReportId,IncidentReportDate,ProductCategoryId,"
                "IncidentProductDescription",
            },
        )

        if findings["sp_recall_sets_status"] == "available":
            rpc = c.odata(
                "RecallProductCategories_top",
                "RecallProductCategories",
                {"$top": pc.sample_top},
            )
            rhd = c.odata(
                "RecallHazardDetails_top",
                "RecallHazardDetails",
                {"$top": pc.sample_top},
            )
            sp_rd = c.odata(
                "RecallDetails_matched",
                "RecallDetails",
                {"$filter": or_filter("RecallId", api_ids)},
            )
            sp_rpc = c.odata(
                "RecallProductCategories_matched",
                "RecallProductCategories",
                {"$filter": or_filter("RecallId", api_ids)},
            )
    except StopProbe as err:
        LOG.error("STOPPED: %s", err)
        raise SystemExit(2) from err

    # ---------- Analysis (local only) ----------
    neiss = neiss_codes(pc.neiss_tsv)

    def name(cid: int | None) -> str | None:
        return cat_ids.get(cid, {}).get("ProductCategoryPublicName") if cid else None

    # Recalls API CategoryID per product
    api_prod = [
        {
            "RecallID": r["RecallID"],
            "RecallNumber": r["RecallNumber"],
            "product": (p.get("Name") or "")[:60],
            "CategoryID": p.get("CategoryID"),
            "Type": p.get("Type"),
        }
        for r in api_recalls
        for p in r.get("Products", [])
    ]
    api_cat_ints = []
    for p in api_prod:
        try:
            api_cat_ints.append(int(p["CategoryID"]))
        except (TypeError, ValueError):
            pass
    sp_rpc_by_id: dict[int, list[int]] = {}
    for row in sp_rpc:
        sp_rpc_by_id.setdefault(row["RecallId"], []).append(row["ProductCategoryId"])

    findings |= {
        "product_category_id_range": [min(cat_ids), max(cat_ids)],
        "product_category_sample": [
            {k: v for k, v in r.items() if k != "__metadata"} for r in cats[:5]
        ],
        "neiss_2025_distinct_codes_any_slot": len(neiss),
        "neiss_codes_in_sp_categories": sum(1 for p in neiss if p in cat_ids),
        "neiss_codes_not_in_sp_categories": sorted(
            p for p in neiss if p not in cat_ids
        ),
        "neiss_p1_rows_covered_pct": round(
            100 * sum(v["n_p1"] for p, v in neiss.items() if p in cat_ids)
            / sum(v["n_p1"] for v in neiss.values()),
            2,
        ),
        "sp_categories_not_in_neiss_2025": sum(1 for p in cat_ids if p not in neiss),
        "top_neiss_codes_with_sp_names": [
            {
                "code": p,
                "neiss_p1_rows": v["n_p1"],
                "sp_name": name(p),
                "neiss_narratives": neiss_narratives(pc.neiss_tsv, p),
            }
            for p, v in sorted(neiss.items(), key=lambda kv: -kv[1]["n_p1"])[:6]
        ],
        "RecallDetails_top_sample": [
            {k: v for k, v in r.items() if k != "__metadata"} for r in rd[:3]
        ],
        "RecallProductCategories_top_sample": [
            {k: v for k, v in r.items() if k != "__metadata"} for r in rpc[:5]
        ],
        "RecallHazardDetails_top_sample": [
            {k: v for k, v in r.items() if k != "__metadata"} for r in rhd[:5]
        ],
        "rpc_category_ids_in_lookup": sum(
            1 for r in rpc if r["ProductCategoryId"] in cat_ids
        ),
        "incident_filter_rows": [
            {k: v for k, v in r.items() if k != "__metadata"} for r in inc
        ],
        "recalls_api_n": len(api_recalls),
        "recalls_api_products_n": len(api_prod),
        "recalls_api_categoryid_values": sorted(
            {str(p["CategoryID"]) for p in api_prod}
        ),
        "recalls_api_categoryid_int_in_sp_lookup": sum(
            1 for x in api_cat_ints if x in cat_ids
        ),
        "recalls_api_categoryid_int_in_neiss": sum(
            1 for x in api_cat_ints if x in neiss
        ),
        "sp_recalldetails_matched_n": len(sp_rd),
        "sp_recalldetails_matched_sample": [
            {k: v for k, v in r.items() if k != "__metadata"} for r in sp_rd[:3]
        ],
        "side_by_side": [
            {
                **p,
                "api_cat_name_in_sp_lookup": name(
                    int(p["CategoryID"]) if str(p["CategoryID"]).isdigit() else None
                ),
                "sp_RecallProductCategories": sp_rpc_by_id.get(p["RecallID"]),
                "sp_names": [name(x) for x in sp_rpc_by_id.get(p["RecallID"], [])],
            }
            for p in api_prod[:25]
        ],
        "requests_made": c.n_requests,
    }
    out = pc.out_dir / "join_probe_findings.json"
    out.write_text(json.dumps(findings, indent=2, default=str))
    LOG.info("Wrote %s (%d requests)", out.relative_to(REPO_ROOT), c.n_requests)


if __name__ == "__main__":
    main()
