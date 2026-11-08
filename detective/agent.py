"""Build the agent (detective or plain mode) and run one chat turn."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from agents import (
    Agent,
    MaxTurnsExceeded,
    ModelSettings,
    RunContextWrapper,
    RunHooks,
    Runner,
    SQLiteSession,
    Tool,
    set_tracing_disabled,
)
from agents.extensions.models.litellm_model import LitellmModel

from detective.config import AgentConfig
from detective.db import Database, load_context
from detective.present import build_caveats, build_chart, build_table
from detective.tools import RunState, make_detective_tools, make_run_sql

# Traces upload to OpenAI by default, which needs an OpenAI key. We're on Gemini.
set_tracing_disabled(True)
LOG = logging.getLogger("detective")

PLAIN_PROMPT = (
    "You are a data analyst. Answer questions with the DuckDB database through "
    "run_sql. Tables: `neiss` (CPSC NEISS emergency-department injury records, "
    "2019-2025, with the original file's columns), `product_codes` (NEISS product code "
    "titles) and `recalls` (CPSC recalls)."
)


class RecordToolCalls(RunHooks[RunState]):
    """Records every completed tool call, with the name of the agent that made it."""

    async def on_tool_end(
        self,
        context: RunContextWrapper[RunState],
        agent: Agent[RunState],
        tool: Tool,
        result: str,
    ) -> None:
        raw = getattr(context, "tool_arguments", None) or "{}"
        try:
            args = json.loads(raw)
        except json.JSONDecodeError:
            args = {"_raw": raw}
        context.context.record(tool.name, args, str(result), agent.name)


@dataclass
class Detective:
    cfg: AgentConfig
    mode: str
    agent: Agent[RunState]
    db: Database

    @classmethod
    def build(cls, cfg: AgentConfig, mode: str | None = None) -> Detective:
        mode = mode or cfg.mode()
        model = LitellmModel(model=cfg.model, api_key="unused")  # Vertex uses ADC
        settings = ModelSettings(extra_args={"vertex_location": cfg.vertex_location})
        if mode == "plain":
            db = Database(cfg, plain=True)
            agent: Agent[RunState] = Agent(
                name="Plain SQL agent",
                instructions=PLAIN_PROMPT,
                model=model,
                model_settings=settings,
                tools=[make_run_sql(db, guarded=False)],
            )
        else:
            db = Database(cfg)
            ctx = load_context(cfg.context_json)
            agent = Agent(
                name="Product-Injury Detective",
                instructions=cfg.system_prompt.read_text(),
                model=model,
                model_settings=settings,
                tools=make_detective_tools(db, ctx, cfg),
            )
        return cls(cfg=cfg, mode=mode, agent=agent, db=db)

    async def ask(
        self, message: str, session: SQLiteSession | None = None
    ) -> dict[str, Any]:
        """One chat turn -> the /chat payload minus session_id."""
        state = RunState()
        try:
            result = await asyncio.wait_for(
                Runner.run(
                    self.agent,
                    message,
                    session=session,
                    context=state,
                    hooks=RecordToolCalls(),
                    max_turns=self.cfg.max_turns,
                ),
                timeout=self.cfg.request_timeout_s,
            )
            response = str(result.final_output or "")
        except MaxTurnsExceeded:
            response = (
                f"I hit my limit of {self.cfg.max_turns} steps before finishing. "
                "Try a narrower question (one product, one age group, one year)."
            )
        except TimeoutError:
            response = (
                "That took too long and was stopped. Try a narrower question."
            )
        except Exception as e:  # auth, quota, a model that is not running
            LOG.exception("agent run failed")
            response = f"Model call failed: {type(e).__name__}: {str(e)[:300]}"
        table = build_table(state)
        return {
            "response": response,
            "tool_calls": state.tool_calls,
            "table": table,
            "chart": build_chart(table, self.cfg),
            "caveats": build_caveats(state, self.cfg) if self.mode != "plain" else [],
            "mode": self.mode,
        }
