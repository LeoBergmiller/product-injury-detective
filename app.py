"""Product-Injury Detective: FastAPI server for the Text2SQL agent.

Run locally:  gcloud auth application-default login && uv run app.py
Cloud Run:    uvicorn app:app --host 0.0.0.0 --port $PORT  (max instances = 1:
              sessions are in memory)
Set AGENT_MODE=plain for the README baseline agent (minimal prompt, raw view, no
guardrails).
"""

from __future__ import annotations

import logging
import uuid
from collections import OrderedDict
from typing import Any

import uvicorn
from agents import SQLiteSession
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from detective.agent import Detective
from detective.config import AgentConfig

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
CFG = AgentConfig.load()
DETECTIVE = Detective.build(CFG)

# --- Session store: in memory, one SQLiteSession(":memory:") per session_id ---
# Least recently used sessions are dropped beyond MAX_SESSIONS, so a long-running
# service (kept up until grades are released) can't grow without bound.
MAX_SESSIONS = 500
sessions: OrderedDict[str, SQLiteSession] = OrderedDict()

app = FastAPI(title="Product-Injury Detective")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None


class ChatResponse(BaseModel):
    response: str
    session_id: str
    tool_calls: list[dict[str, Any]]
    table: dict[str, Any] | None = None
    chart: dict[str, Any] | None = None
    caveats: list[str] = []
    mode: str = "detective"


@app.get("/")
def index() -> FileResponse:
    return FileResponse(CFG.frontend)


@app.get("/health")
def health() -> dict[str, Any]:
    res = DETECTIVE.db.query("SELECT 1 AS ok")
    return {"status": "ok" if "rows" in res else "error", "mode": DETECTIVE.mode}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    session_id = request.session_id or str(uuid.uuid4())
    session = sessions.get(session_id) or SQLiteSession(session_id)
    sessions[session_id] = session
    sessions.move_to_end(session_id)
    while len(sessions) > MAX_SESSIONS:
        sessions.popitem(last=False)
    payload = await DETECTIVE.ask(request.message, session=session)
    return ChatResponse(session_id=session_id, **payload)


@app.post("/clear")
async def clear(session_id: str | None = None) -> dict[str, str]:
    session = sessions.pop(session_id, None) if session_id else None
    if session is not None:
        await session.clear_session()
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
