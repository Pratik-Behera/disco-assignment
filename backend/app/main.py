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

from app.agents import iter_run
from app.agents import run_ads
from app.data import load_examples, load_publishers
from app.llm import llm_enabled


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

# thread_id → original query plus answers so resume does not start over.
# ponytail: in-process only, so a reload or a second worker drops pending threads.
# Resume then 400s and the client starts over. Move to Redis if that matters.
_PENDING_MAX = 256
_pending: OrderedDict[str, dict] = OrderedDict()


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


class RunIn(BaseModel):
    raw_input: str = ""
    thread_id: str | None = None
    resume: str | None = None
    skip: bool = False


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
            "llm": llm_enabled(),
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
        if body.skip or body.resume:
            if not body.thread_id:
                raise HTTPException(400, "thread_id is required when resuming a clarify answer")
            thread_id = body.thread_id
            if thread_id not in _pending:
                raise HTTPException(400, "Unknown or expired thread_id — start a new run")
            pending = _pending.pop(thread_id)
            user_text = pending["query"]
            answers = list(pending.get("answers") or [])
            skipped = list(pending.get("skipped") or [])
            asked = list(pending.get("asked") or [])
            field = pending.get("field")
            pending_snapshot = pending.get("snapshot") or {}
            if field:
                asked.append(field)
            if body.skip:
                skipped.append(field or "target_audience")
                clarification = None
            elif field:
                # The answer already carries the text; a clarification too would double it.
                answers.append({"field": field, "value": body.resume})
                clarification = None
            else:
                clarification = body.resume
        else:
            thread_id = str(uuid.uuid4())
            user_text, clarification = body.raw_input, None
            answers, skipped, asked = [], [], []
            pending_snapshot = {}

        ads_only = bool(pending_snapshot) and bool(body.skip or body.resume)
        snapshot = pending_snapshot if ads_only else {}

        def events() -> Iterator[str]:
            def _store(field: str | None, snap: dict) -> None:
                _pending[thread_id] = {
                    "query": user_text,
                    "answers": answers,
                    "skipped": skipped,
                    "asked": asked,
                    "field": field,
                    "snapshot": snap,
                }
                while len(_pending) > _PENDING_MAX:
                    _pending.popitem(last=False)

            def _clarify(meta: dict, question: str | None = None) -> str:
                return _sse(
                    "clarify",
                    {"thread_id": thread_id, "question": meta.get("question") or question, **meta},
                )

            def _section(stage: str, kind: str, text: str) -> Iterator[str]:
                if not text:
                    return
                yield _sse("stage", {"stage": stage})
                yield _sse("section", {"kind": kind})
                for token in _token_chunks(text):
                    yield _sse("token", {"text": token})

            def _done(result) -> str:
                return _sse(
                    "done",
                    {
                        "thread_id": thread_id,
                        "text": result.text or "",
                        "chosen": result.chosen or [],
                        "personas": result.personas or [],
                        "creatives": result.creatives or [],
                    },
                )

            try:
                if ads_only:
                    yield _sse("stage", {"stage": "creatives"})
                    result = run_ads(snapshot, answers=answers)
                    yield from _section("creatives", "ads", result.ads_text)
                    yield _done(result)
                    return

                yield _sse("stage", {"stage": "understand"})
                last = None
                finished = False
                for node, result in iter_run(
                    user_text,
                    clarification=clarification,
                    answers=answers,
                    skipped_fields=skipped,
                    asked_fields=asked,
                ):
                    last = result
                    if finished:
                        continue
                    if node == "halt_required":
                        meta = result.question_meta or {}
                        _store(meta.get("field"), {})
                        yield _clarify(meta, result.question)
                        finished = True
                        continue
                    if node == "ready_to_place":
                        yield _sse("stage", {"stage": "publishers"})
                    if node == "assemble_result":
                        yield from _section("publishers", "publishers", result.publishers_text)
                        yield from _section("personas", "personas", result.personas_text)
                        if result.question_meta:
                            _store(result.question_meta.get("field"), result.snapshot)
                            yield _clarify(result.question_meta)
                            finished = True
                            continue
                        yield _sse("stage", {"stage": "creatives"})
                    if node == "validate_creatives":
                        yield from _section("creatives", "ads", result.ads_text)
                        yield _done(result)
                        finished = True
                        continue
                if last is not None and not finished:
                    yield _done(last)
            except Exception:
                log.exception("agent run failed")
                yield _sse("error", {"detail": "Something went wrong. Try again."})

        return StreamingResponse(events(), media_type="text/event-stream")

    return app


def _token_chunks(text: str) -> Iterator[str]:
    """Word-at-a-time, keeping the trailing whitespace so newlines survive the stream."""
    for chunk in re.findall(r"\S+\s*", text):
        yield chunk
        time.sleep(0.01)


app = create_app()
