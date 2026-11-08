"""Build the agent (detective or plain mode) and run one chat turn."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from agents import (
    Agent,
    ItemHelpers,
    MaxTurnsExceeded,
    MessageOutputItem,
    ModelSettings,
    RunContextWrapper,
    RunErrorHandlerInput,
    RunHooks,
    Runner,
    SQLiteSession,
    Tool,
    set_tracing_disabled,
)
from agents.extensions.models.litellm_model import LitellmModel

from detective.config import AgentConfig
from detective.db import Database, load_context
from detective.grounding import normalize_visits, ungrounded_numbers
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


WRAP_UP = (
    "You have reached the step limit. Do not call tools. Answer the question now, "
    "using only numbers from the tool results above. If the data cannot answer it, "
    "say so in your first sentence and why."
)


FRAGMENT_CHARS = 160
GROUNDING_FIX = (
    "Your answer contains numbers that do not appear in any tool result: {numbers}. "
    "Rewrite the whole answer using only numbers that appear in the tool results "
    "above (rounding is fine). Do not call tools. Keep the same structure."
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

    async def _wrap_up(self, data: RunErrorHandlerInput[RunState]) -> str | None:
        """At the turn limit, answer once more WITHOUT tools from what was found."""
        closer = data.run_data.last_agent.clone(tools=[])
        history = list(data.run_data.history) + [
            {"role": "user", "content": WRAP_UP}
        ]
        try:
            result = await Runner.run(closer, history, context=data.context.context,
                                      max_turns=1)
            return str(result.final_output or "") or None
        except Exception:  # fall back to the fixed message in ask()
            LOG.exception("wrap-up after max_turns failed")
            return None

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
                    error_handlers={"max_turns": self._wrap_up},
                ),
                timeout=self.cfg.request_timeout_s,
            )
            response = self._full_text(result) or (
                f"I hit my limit of {self.cfg.max_turns} steps before finishing. "
                "Try a narrower question (one product, one age group, one year)."
            )
            if self.mode != "plain":
                response = await self._ground(result, response, state)
                response = normalize_visits(response)
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
        caveats = build_caveats(state, self.cfg) if self.mode != "plain" else []
        if state.ungrounded:
            caveats.append(
                "Some numbers in this answer could not be matched to a query result: "
                + ", ".join(state.ungrounded)
                + ". Check them against the table and the tool calls."
            )
        return {
            "response": response,
            "tool_calls": state.tool_calls,
            "table": table,
            "chart": build_chart(table, self.cfg),
            "caveats": caveats,
            "mode": self.mode,
        }

    @staticmethod
    def _full_text(result: Any) -> str:
        """The final message; if it is only a short fragment, all of this turn's
        assistant text (Gemini sometimes puts the answer before its last tool call)."""
        final = str(result.final_output or "").strip()
        if len(final) >= FRAGMENT_CHARS:
            return final
        texts = [
            ItemHelpers.text_message_output(i).strip()
            for i in result.new_items
            if isinstance(i, MessageOutputItem)
        ]
        texts = [t for t in texts if t]
        return "\n\n".join(texts) if len(texts) > 1 else final

    async def _ground(self, result: Any, response: str, state: RunState) -> str:
        """Rewrite once (no tools) if the answer has numbers no tool returned."""
        bad = ungrounded_numbers(response, state.tool_calls)
        if not bad:
            return response
        closer = self.agent.clone(tools=[])
        history = result.to_input_list() + [
            {"role": "user", "content": GROUNDING_FIX.format(numbers=", ".join(bad))}
        ]
        try:
            fixed = await Runner.run(closer, history, context=state, max_turns=1)
            new = str(fixed.final_output or "").strip()
        except Exception:
            LOG.exception("grounding rewrite failed")
            new = ""
        still = ungrounded_numbers(new, state.tool_calls) if new else bad
        use_new = bool(new) and len(still) < len(bad)
        state.tool_calls.append({
            "name": "grounding_check",
            "args": {"unmatched_numbers": bad},
            "result": "answer rewritten" if use_new else "rewrite not used",
            "agent": self.agent.name,
        })
        state.ungrounded = still if use_new else bad
        return new if use_new else response
