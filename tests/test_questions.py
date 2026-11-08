"""Live agent checks: ask every question in tests/questions.yaml and apply its checks.

Needs Vertex AI access (gcloud application-default credentials). Each question runs
in a fresh session. Results go to tests/results/questions_<mode>.json.

Usage: uv run python tests/test_questions.py [--mode detective|plain] [--only ID]
Exit code 1 if any question fails.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from detective.agent import Detective  # noqa: E402
from detective.config import AgentConfig  # noqa: E402

LOG = logging.getLogger("test_questions")
QUESTIONS = Path(__file__).with_name("questions.yaml")
RESULTS_DIR = Path(__file__).with_name("results")


def check(q: dict[str, Any], out: dict[str, Any]) -> list[str]:
    """Return the failed checks (empty list = pass)."""
    fails: list[str] = []
    text = out["response"]
    called = {c["name"] for c in out["tool_calls"]}
    if q.get("tools_any") and not called & set(q["tools_any"]):
        fails.append(f"none of {q['tools_any']} called (called {sorted(called)})")
    for t in q.get("tools_none", []):
        if t in called:
            fails.append(f"{t} should not be called")
    for group in q.get("text_any", []):
        if not any(re.search(p, text, re.IGNORECASE | re.DOTALL) for p in group):
            fails.append(f"answer matches none of {group}")
    for p in q.get("text_none", []):
        if re.search(p, text, re.IGNORECASE | re.DOTALL):
            fails.append(f"answer matches forbidden /{p}/")
    if q["kind"] != "cannot" and re.match(
        r"\s*(the data|i|this data)\s+(cannot|can't|can not)", text, re.IGNORECASE
    ):
        fails.append("answerable question opens with a 'cannot answer' disclaimer")
    if text.startswith(("Model call failed", "I hit my limit")):
        fails.append("run did not finish: " + text[:120])
    return fails


async def main_async(mode: str, only: str | None) -> int:
    cfg = AgentConfig.load()
    det = Detective.build(cfg, mode)
    qs = yaml.safe_load(QUESTIONS.read_text())["questions"]
    if only:
        qs = [q for q in qs if q["id"] == only]
    results = []
    for q in qs:
        out = await det.ask(q["question"])
        fails = check(q, out)
        rejected = sum('"rejected"' in c["result"] for c in out["tool_calls"])
        LOG.info(
            "%-4s %-24s %-7s tools=%d rejected=%d %s",
            "PASS" if not fails else "FAIL", q["id"], q["kind"],
            len(out["tool_calls"]), rejected, "; ".join(fails),
        )
        results.append({"id": q["id"], "kind": q["kind"], "question": q["question"],
                        "expect": q.get("expect"), "pass": not fails, "fails": fails,
                        **out})
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"questions_{mode}.json"
    path.write_text(json.dumps(results, indent=1, default=str) + "\n")
    n_pass = sum(r["pass"] for r in results)
    LOG.info("%d/%d passed (%s mode); details in %s", n_pass, len(results), mode,
             path.relative_to(REPO_ROOT))
    return 0 if n_pass == len(results) else 1


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for noisy in ("LiteLLM", "httpx", "litellm"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="detective", choices=["detective", "plain"])
    ap.add_argument("--only")
    a = ap.parse_args()
    sys.exit(asyncio.run(main_async(a.mode, a.only)))


if __name__ == "__main__":
    main()
