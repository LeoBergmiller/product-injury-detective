"""Run the README comparison cases repeatedly in both modes and save every trace.

Each case (build/config.yaml, agent.readme_cases) is asked `readme_runs.plain` times
to the plain agent (minimal prompt, raw `neiss` view, run_sql only, no guardrails)
and `readme_runs.detective` times to the detective. Every run is saved and classified
by the first configured outcome its answer matches; nothing is dropped, so the rates
in the summary are the real ones. Needs Vertex AI access (gcloud ADC).

Writes docs/traces/<case>.<mode>.run<N>.json and docs/traces/readme_cases.md.
Usage: uv run python build/run_readme_cases.py [--only CASE_ID] [--reclassify]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from detective.agent import Detective  # noqa: E402
from detective.config import CONFIG_PATH, AgentConfig  # noqa: E402

LOG = logging.getLogger("run_readme_cases")
MODES = ("plain", "detective")


def classify(case: dict[str, Any], answer: str) -> str:
    for o in case["outcomes"]:
        if re.search(o["pattern"], answer, re.IGNORECASE):
            return o["label"]
    return "other (none of the known numbers)"


def sql_steps(out: dict[str, Any]) -> list[str]:
    return [
        ("[REJECTED] " if '"rejected"' in c["result"] else "")
        + " ".join(str(c["args"].get("query", "")).split())
        for c in out["tool_calls"]
        if c["name"] == "run_sql"
    ]


def run_md(n: int, out: dict[str, Any], outcome: str) -> list[str]:
    answer = " ".join(out["response"].split())
    lines = [f"- **Run {n}: {outcome}**", f"  - Answer: {answer[:400]}"
             + (" ..." if len(answer) > 400 else "")]
    for q in sql_steps(out):
        lines.append(f"  - SQL: `{q[:300]}`")
    return lines


def write_summary(
    cfg: AgentConfig, cases: list[dict[str, Any]], runs: dict[str, int],
    records: list[dict[str, Any]], stamp: str,
) -> None:
    summary = [
        "# README cases: plain run_sql agent vs Product-Injury Detective",
        "",
        f"Generated {stamp} by `build/run_readme_cases.py`, model `{cfg.model}`.",
        f"Each case was run {runs['plain']} times in plain mode and "
        f"{runs['detective']} times in detective mode. **Every run is listed**; "
        "outcomes are classified automatically from the numbers in the answer.",
        "Plain = minimal prompt, raw `neiss` view (original file columns) plus "
        "`product_codes` titles, run_sql only, no guardrails.",
        "",
    ]
    details: list[str] = []
    for case in cases:
        summary += [f"## {case['id']}", "", f"**Question:** {case['question']}", "",
                    f"**Verified answer (SQL on the DB):** {case['expected']}", "",
                    "| mode | runs | outcome | count |", "|---|---|---|---|"]
        details += [f"## {case['id']}: every run", ""]
        for mode in MODES:
            mine = sorted((r for r in records
                           if r["case"] == case["id"] and r["mode"] == mode),
                          key=lambda r: r["run"])
            counts: Counter[str] = Counter()
            details += [f"### {mode}", ""]
            for r in mine:
                outcome = classify(case, r["response"])
                counts[outcome] += 1
                details += run_md(r["run"], r, outcome)
            details.append("")
            for outcome, k in counts.most_common():
                summary.append(f"| {mode} | {len(mine)} | {outcome} | {k} |")
        summary.append("")
    (cfg.traces_dir / "readme_cases.md").write_text("\n".join(summary + details))
    LOG.info("wrote %s", (cfg.traces_dir / "readme_cases.md").relative_to(REPO_ROOT))


async def main_async(only: str | None, reclassify: bool) -> None:
    cfg = AgentConfig.load()
    raw = yaml.safe_load(CONFIG_PATH.read_text())["agent"]
    cases, runs = raw["readme_cases"], raw["readme_runs"]
    if only:
        cases = [c for c in cases if c["id"] == only]
    cfg.traces_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    if reclassify:
        # Rebuild the summary from the saved traces (no model calls).
        for case in cases:
            for path in sorted(cfg.traces_dir.glob(f"{case['id']}.*.run*.json")):
                rec = json.loads(path.read_text())
                rec["outcome"] = classify(case, rec["response"])
                path.write_text(json.dumps(rec, indent=1, default=str) + "\n")
                records.append(rec)
        stamp = records[0]["generated"] if records else "n/a"
        write_summary(cfg, cases, runs, records, stamp)
        return
    agents = {m: Detective.build(cfg, m) for m in MODES}
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    for case in cases:
        for mode in MODES:
            for n in range(1, runs[mode] + 1):
                LOG.info("%s / %s / run %d", case["id"], mode, n)
                out = await agents[mode].ask(case["question"])
                record = {"case": case["id"], "question": case["question"],
                          "mode": mode, "run": n, "model": cfg.model,
                          "generated": stamp,
                          "outcome": classify(case, out["response"]), **out}
                path = cfg.traces_dir / f"{case['id']}.{mode}.run{n}.json"
                path.write_text(json.dumps(record, indent=1, default=str) + "\n")
                records.append(record)
    write_summary(cfg, cases, runs, records, stamp)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for noisy in ("LiteLLM", "httpx", "litellm"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="run one case id")
    ap.add_argument("--reclassify", action="store_true",
                    help="rebuild readme_cases.md from saved traces, no model calls")
    a = ap.parse_args()
    asyncio.run(main_async(a.only, a.reclassify))


if __name__ == "__main__":
    main()
