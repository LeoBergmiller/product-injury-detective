"""Ask the same model the README sample questions with no tools and no system prompt.

The README shows these answers next to the agent's (rubric: README section).
Needs Vertex AI access (gcloud application-default credentials).

Writes docs/traces/baseline_no_tools.json and docs/traces/baseline_no_tools.md.
Usage: uv run python build/baseline_no_tools.py
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import litellm
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from detective.config import CONFIG_PATH, AgentConfig  # noqa: E402

LOG = logging.getLogger("baseline_no_tools")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    cfg = AgentConfig.load()
    questions = yaml.safe_load(CONFIG_PATH.read_text())["agent"]["sample_questions"]
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    records = []
    for q in questions:
        LOG.info("asking: %s", q)
        resp = litellm.completion(
            model=cfg.model,
            vertex_location=cfg.vertex_location,
            messages=[{"role": "user", "content": q}],  # no system prompt, no tools
        )
        records.append({"question": q, "answer": resp.choices[0].message.content})
    cfg.traces_dir.mkdir(parents=True, exist_ok=True)
    (cfg.traces_dir / "baseline_no_tools.json").write_text(
        json.dumps({"model": cfg.model, "generated": stamp, "answers": records},
                   indent=1) + "\n"
    )
    md = [
        "# Same model, no tools, no system prompt",
        "",
        f"Generated {stamp} by `build/baseline_no_tools.py`, model `{cfg.model}`.",
        "",
    ]
    for r in records:
        md += [f"## {r['question']}", "", r["answer"].strip(), ""]
    (cfg.traces_dir / "baseline_no_tools.md").write_text("\n".join(md))
    LOG.info("wrote docs/traces/baseline_no_tools.{json,md}")


if __name__ == "__main__":
    main()
