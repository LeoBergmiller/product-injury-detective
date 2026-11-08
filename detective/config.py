"""Runtime config for the agent, read from build/config.yaml (section `agent`)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "build" / "config.yaml"


@dataclass(frozen=True)
class AgentConfig:
    model: str
    vertex_location: str
    max_turns: int
    max_rows: int
    sql_timeout_s: float
    request_timeout_s: float
    db_path: Path
    allowed_dir: Path
    context_json: Path
    system_prompt: Path
    frontend: Path
    mode_env: str
    chart_max_rows: int
    recall_limit: int
    plain_neiss_columns: dict[str, str]
    traces_dir: Path
    min_cases: int
    placeholder_codes: list[int]

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> AgentConfig:
        raw = yaml.safe_load(path.read_text())
        a = raw["agent"]
        return cls(
            model=a["model"],
            vertex_location=a["vertex_location"],
            max_turns=int(a["max_turns"]),
            max_rows=int(a["max_rows"]),
            sql_timeout_s=float(a["sql_timeout_s"]),
            request_timeout_s=float(a["request_timeout_s"]),
            db_path=REPO_ROOT / a["db_path"],
            allowed_dir=REPO_ROOT / a["allowed_dir"],
            context_json=REPO_ROOT / a["context_json"],
            system_prompt=REPO_ROOT / a["system_prompt"],
            frontend=REPO_ROOT / a["frontend"],
            mode_env=a["mode_env"],
            chart_max_rows=int(a["chart_max_rows"]),
            recall_limit=int(a["recall_limit"]),
            plain_neiss_columns=dict(a["plain_neiss_columns"]),
            traces_dir=REPO_ROOT / a["traces_dir"],
            min_cases=int(raw["build_db"]["min_cases"]),
            placeholder_codes=[
                int(c) for c in raw["product_codes"]["placeholder_codes"]
            ],
        )

    def mode(self) -> str:
        """'plain' (README baseline) or 'detective' (default)."""
        return "plain" if os.environ.get(self.mode_env, "").lower() == "plain" else (
            "detective"
        )
