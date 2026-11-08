"""Run the README comparison cases in both modes and save the traces.

Each case (build/config.yaml, agent.readme_cases) is asked once to the plain agent
(minimal prompt, raw `neiss` view, run_sql only, no guardrails) and once to the
detective. Needs Vertex AI access (gcloud application-default credentials).

Writes docs/traces/<case>.<mode>.json and docs/traces/readme_cases.md.
Usage: uv run python build/run_readme_cases.py [--only CASE_ID]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
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


def trace_md(case: dict[str, Any], mode: str, out: dict[str, Any]) -> str:
    lines = [f"### {mode}", "", "**Answer:**", "", "> " + out["response"].replace(
        "\n", "\n> "), "", "**Tool calls:**", ""]
    for i, c in enumerate(out["tool_calls"], 1):
        is_sql = c["name"] == "run_sql"
        arg = c["args"].get("query") if is_sql else json.dumps(c["args"])
        res = c["result"] if len(c["result"]) < 400 else c["result"][:400] + " ..."
        lines += [
            f"{i}. `{c['name']}` ({c['agent']})",
            "",
            "   ```sql" if c["name"] == "run_sql" else "   ```json",
            *("   " + ln for ln in str(arg).splitlines()),
            "   ```",
            f"   Result: `{res}`",
            "",
        ]
    return "\n".join(lines)


async def main_async(only: str | None) -> None:
    cfg = AgentConfig.load()
    cases = yaml.safe_load(CONFIG_PATH.read_text())["agent"]["readme_cases"]
    if only:
        cases = [c for c in cases if c["id"] == only]
    cfg.traces_dir.mkdir(parents=True, exist_ok=True)
    agents = {m: Detective.build(cfg, m) for m in MODES}
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    md = [
        "# README cases: plain run_sql agent vs Product-Injury Detective",
        "",
        f"Generated {stamp} by `build/run_readme_cases.py`, model `{cfg.model}`.",
        "Plain = minimal prompt, raw `neiss` view (original file columns),",
        "`product_codes` titles, run_sql only, no guardrails. Each case isolates",
        "one cause. Raw JSON traces are next to this file.",
        "",
    ]
    for case in cases:
        md += [f"## {case['id']}", "", f"**Question:** {case['question']}", "",
               f"**Verified answer:** {case['expected']}", ""]
        for mode in MODES:
            LOG.info("%s / %s", case["id"], mode)
            out = await agents[mode].ask(case["question"])
            record = {"case": case, "mode": mode, "model": cfg.model,
                      "generated": stamp, **out}
            path = cfg.traces_dir / f"{case['id']}.{mode}.json"
            path.write_text(json.dumps(record, indent=1, default=str) + "\n")
            md += [trace_md(case, mode, out), ""]
    (cfg.traces_dir / "readme_cases.md").write_text("\n".join(md))
    LOG.info("wrote %s", (cfg.traces_dir / "readme_cases.md").relative_to(REPO_ROOT))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for noisy in ("LiteLLM", "httpx", "litellm"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="run one case id")
    asyncio.run(main_async(ap.parse_args().only))


if __name__ == "__main__":
    main()
