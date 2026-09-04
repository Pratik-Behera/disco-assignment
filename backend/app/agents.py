"""Entry: required missing first, then rank ║ personas, then ads if no shopper question."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from app.creative import generate_creatives, validate_creatives
from app.graph import _apply_answers, get_graph
from app.personas import catalog_personas
from app.schemas import AdvertiserProfile, PersonaMatch, PublisherContext


@dataclass
class AgentResult:
    text: str | None = None
    question: str | None = None
    question_meta: dict | None = None
    chosen: list[dict] | None = None
    personas: list[dict] | None = None
    creatives: list[dict] | None = None
    publishers_text: str = ""
    personas_text: str = ""
    ads_text: str = ""
    snapshot: dict = field(default_factory=dict)


def _inputs(
    user_text: str,
    *,
    clarification: str | None = None,
    answers: list[dict] | None = None,
    skipped_fields: list[str] | None = None,
    asked_fields: list[str] | None = None,
) -> dict:
    return {
        "raw_query": user_text,
        "clarification": clarification,
        "answers": answers or [],
        "skipped_fields": skipped_fields or [],
        "asked_fields": asked_fields or [],
    }


def _to_result(state: dict) -> AgentResult:
    meta = state.get("question_meta")
    public = meta.to_public() if meta else None
    if state.get("status") == "insufficient_signal" and public:
        return AgentResult(question=public["question"], question_meta=public)
    ads = [row.to_public() for row in (state.get("creatives") or [])]
    personas = [row.to_public() for row in (state.get("persona_matches") or [])]
    profile = state.get("profile")
    return AgentResult(
        text=state.get("text") or "",
        chosen=state.get("chosen") or [],
        question_meta=public if public and public.get("importance") == "useful" else None,
        personas=personas,
        creatives=ads,
        publishers_text=state.get("publishers_text") or "",
        personas_text=state.get("personas_text") or "",
        ads_text=state.get("ads_text") or "",
        snapshot={
            "profile": profile.model_dump() if profile else {},
            "contexts": [ctx.model_dump() for ctx in (state.get("publisher_contexts") or [])],
            "matches": [row.model_dump() for row in (state.get("persona_matches") or [])],
            "chosen": state.get("chosen") or [],
            "personas": personas,
        },
    )


def run(
    user_text: str,
    *,
    clarification: str | None = None,
    answers: list[dict] | None = None,
    skipped_fields: list[str] | None = None,
    asked_fields: list[str] | None = None,
) -> AgentResult:
    state = get_graph().invoke(
        _inputs(
            user_text,
            clarification=clarification,
            answers=answers,
            skipped_fields=skipped_fields,
            asked_fields=asked_fields,
        )
    )
    return _to_result(state)


def iter_run(
    user_text: str,
    *,
    clarification: str | None = None,
    answers: list[dict] | None = None,
    skipped_fields: list[str] | None = None,
    asked_fields: list[str] | None = None,
) -> Iterator[tuple[str, AgentResult]]:
    """Yield (node, result) after each graph node so SSE can reveal sections early."""
    inputs = _inputs(
        user_text,
        clarification=clarification,
        answers=answers,
        skipped_fields=skipped_fields,
        asked_fields=asked_fields,
    )
    acc: dict = dict(inputs)
    stream = get_graph().stream(inputs, stream_mode="updates")
    try:
        for chunk in stream:
            for node, delta in chunk.items():
                if isinstance(delta, dict):
                    acc.update(delta)
                yield node, _to_result(acc)
    except GeneratorExit:
        return


def run_ads(snapshot: dict, answers: list[dict] | None = None) -> AgentResult:
    """Resume after a useful shopper question: ads only, no re-rank."""
    profile = _apply_answers(AdvertiserProfile.model_validate(snapshot["profile"]), answers)
    contexts = [PublisherContext.model_validate(row) for row in snapshot.get("contexts") or []]
    matches = [PersonaMatch.model_validate(row) for row in snapshot.get("matches") or []]
    picked = " ".join((ans.get("value") or "") for ans in (answers or [])).lower()
    if picked:
        preferred = [row for row in matches if picked in row.persona_name.lower()]
        if preferred:
            rest = [row for row in matches if row not in preferred]
            matches = preferred + rest
    variants = validate_creatives(
        generate_creatives(profile, contexts, matches, catalog_personas()),
        profile,
    )
    ads_text = ""
    if variants:
        lines = ["Ads"]
        for item in variants:
            lines.append(item.headline)
            lines.append(f"• {item.body}")
            lines.append(f"• {item.cta}")
        ads_text = "\n".join(lines)
    creatives = [row.to_public() for row in variants]
    return AgentResult(
        text=ads_text,
        chosen=snapshot.get("chosen") or [],
        personas=snapshot.get("personas") or [],
        creatives=creatives,
        ads_text=ads_text,
    )
