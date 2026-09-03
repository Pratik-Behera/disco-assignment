"""Thin FastAPI shell: examples, health, SSE chat stream."""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from collections import OrderedDict
from collections.abc import Iterator
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agents import run as agent_run
from app.data import load_examples, load_publishers


def _load_env() -> None:
    path = Path(__file__).resolve().parents[1] / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# LLM and LangSmith env is read on first graph invoke, so loading here is early enough.
_load_env()

log = logging.getLogger(__name__)

# thread_id → original user query (for clarify resume)
# ponytail: in-process only, so a reload or a second worker drops pending threads.
# Resume then 400s and the client starts over. Move to Redis if that matters.
_PENDING_MAX = 256
_pending: OrderedDict[str, str] = OrderedDict()


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


class RunIn(BaseModel):
    raw_input: str = ""
    thread_id: str | None = None
    resume: str | None = None


def create_app() -> FastAPI:
    app = FastAPI(title="Disco campaign builder")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.examples = load_examples()
    app.state.publishers = load_publishers()

    @app.get("/")
    def root() -> dict[str, str]:
        return {"service": "disco", "ui": "http://127.0.0.1:5173"}

    @app.get("/api/health")
    def health() -> dict[str, object]:
        return {
            "ok": True,
            "publishers": len(app.state.publishers),
            "llm": bool(os.environ.get("LLM_API_KEY")),
            "langsmith": (
                os.environ.get("LANGSMITH_TRACING", "").lower() in {"1", "true", "yes"}
                and bool(os.environ.get("LANGSMITH_API_KEY"))
            ),
        }

    @app.get("/api/examples")
    def list_examples() -> dict[str, list[str]]:
        return {"examples": app.state.examples}

    @app.post("/api/run/stream")
    def run_stream(body: RunIn) -> StreamingResponse:
        if body.resume:
            if not body.thread_id:
                raise HTTPException(400, "thread_id is required when resuming a clarify answer")
            thread_id = body.thread_id
            if thread_id not in _pending:
                raise HTTPException(400, "Unknown or expired thread_id — start a new run")
            user_text, clarification = _pending.pop(thread_id), body.resume
        else:
            thread_id = str(uuid.uuid4())
            user_text, clarification = body.raw_input, None

        def events() -> Iterator[str]:
            yield _sse("stage", {"stage": "read"})
            try:
                result = agent_run(user_text, clarification=clarification)
            except Exception:
                # Provider errors quote the failing request, keys included. Log, don't stream.
                log.exception("agent run failed")
                yield _sse("error", {"detail": "Something went wrong. Try again."})
                return

            if result.question:
                _pending[thread_id] = user_text
                while len(_pending) > _PENDING_MAX:
                    _pending.popitem(last=False)
                yield _sse("clarify", {"thread_id": thread_id, "question": result.question})
                return

            yield _sse("stage", {"stage": "write"})
            text = result.text or ""
            for token in _token_chunks(text):
                yield _sse("token", {"text": token})
            yield _sse(
                "done",
                {
                    "thread_id": thread_id,
                    "text": text,
                    "chosen": result.chosen or [],
                },
            )

        return StreamingResponse(events(), media_type="text/event-stream")

    return app


def _token_chunks(text: str) -> Iterator[str]:
    """Word-at-a-time, keeping the trailing whitespace so newlines survive the stream."""
    for chunk in re.findall(r"\S+\s*", text):
        yield chunk
        time.sleep(0.01)


app = create_app()
