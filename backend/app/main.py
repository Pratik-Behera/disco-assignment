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

from app.agents import iter_run, run_ads, run_campaign
from app.campaign import is_campaign_revision
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


def _ui_dir() -> Path | None:
    raw = os.environ.get("DISCO_UI_DIR")
    path = Path(raw) if raw else Path(__file__).resolve().parents[1] / "static"
    return path if (path / "index.html").is_file() else None


def _mount_ui(app: FastAPI, ui: Path) -> None:
    # Path operations win over frontend(); do not register GET / when this runs.
    if hasattr(app, "frontend"):
        app.frontend("/", directory=ui, fallback="index.html")
        return
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=ui, html=True), name="ui")


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
    ui = _ui_dir()
    if ui is None:

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
            pending_phase = pending.get("phase") or ""
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
            raw_update = None
        elif body.thread_id and body.raw_input and body.thread_id in _pending:
            pending = _pending[body.thread_id]
            if pending.get("phase") == "revision" and is_campaign_revision(body.raw_input):
                thread_id = body.thread_id
                user_text = pending["query"]
                # The snapshot's campaign_inputs already folds in every earlier answer
                # and revision; replaying the answers would undo the newer revisions.
                answers = []
                skipped = list(pending.get("skipped") or [])
                asked = list(pending.get("asked") or [])
                pending_snapshot = pending.get("snapshot") or {}
                pending_phase = "revision"
                clarification = None
                raw_update = body.raw_input
            else:
                _pending.pop(body.thread_id, None)
                thread_id = str(uuid.uuid4())
                user_text, clarification = body.raw_input, None
                answers, skipped, asked = [], [], []
                pending_snapshot, pending_phase, raw_update = {}, "", None
        else:
            thread_id = str(uuid.uuid4())
            user_text, clarification = body.raw_input, None
            answers, skipped, asked = [], [], []
            pending_snapshot, pending_phase, raw_update = {}, "", None

        ads_only = pending_phase == "ads"
        campaign_only = pending_phase in {"campaign", "revision"}
        snapshot = pending_snapshot if (ads_only or campaign_only) else {}

        def events() -> Iterator[str]:
            def _store(field: str | None, snap: dict, phase: str = "") -> None:
                _pending[thread_id] = {
                    "query": user_text,
                    "answers": answers,
                    "skipped": skipped,
                    "asked": asked,
                    "field": field,
                    "snapshot": snap,
                    "phase": phase,
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

            def _campaign_followup(snap: dict) -> Iterator[str]:
                result = run_campaign(
                    snap,
                    answers=answers,
                    skipped_fields=skipped,
                    asked_fields=asked,
                    raw_update=raw_update,
                )
                if result.question_meta:
                    _store(result.question_meta.get("field"), result.snapshot, "campaign")
                    yield _clarify(result.question_meta)
                    return
                yield _sse("stage", {"stage": "campaign"})
                yield from _section("campaign", "campaign", result.campaign_text)
                _store(None, result.snapshot, "revision")
                yield _done(result)

            try:
                if ads_only:
                    if body.skip:
                        yield from _campaign_followup(snapshot)
                        return
                    yield _sse("stage", {"stage": "creatives"})
                    result = run_ads(snapshot, answers=answers)
                    yield from _section("creatives", "ads", result.ads_text)
                    yield from _campaign_followup({**snapshot, **result.snapshot, "raw_query": user_text})
                    return
                if campaign_only:
                    yield from _campaign_followup({**snapshot, "raw_query": snapshot.get("raw_query") or user_text})
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
                        if not (result.chosen or []):
                            yield _done(result)
                            finished = True
                            continue
                        yield _sse("stage", {"stage": "creatives"})
                    if node == "validate_creatives":
                        yield from _section("creatives", "ads", result.ads_text)
                        if result.question_meta:
                            _store(result.question_meta.get("field"), result.snapshot, "ads")
                            yield _clarify(result.question_meta)
                            finished = True
                            continue
                    if node == "campaign_input_analysis" and result.question_meta:
                        _store(result.question_meta.get("field"), result.snapshot, "campaign")
                        yield _clarify(result.question_meta)
                        finished = True
                        continue
                    if node == "campaign_llm_strategist":
                        yield _sse("stage", {"stage": "campaign"})
                        yield from _section("campaign", "campaign", result.campaign_text)
                        _store(None, result.snapshot, "revision")
                        yield _done(result)
                        finished = True
                        continue
                if last is not None and not finished:
                    yield _done(last)
            except Exception:
                log.exception("agent run failed")
                yield _sse("error", {"detail": "Something went wrong. Try again."})

        return StreamingResponse(events(), media_type="text/event-stream")

    if ui is not None:
        _mount_ui(app, ui)
    return app


def _token_chunks(text: str) -> Iterator[str]:
    """Word-at-a-time, keeping the trailing whitespace so newlines survive the stream."""
    for chunk in re.findall(r"\S+\s*", text):
        yield chunk
        time.sleep(0.01)


app = create_app()
