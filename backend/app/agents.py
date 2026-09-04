"""Entry: required missing first, then rank ║ personas, then ads, then campaign."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from app.campaign import (
    analyze_campaign_missing,
    apply_campaign_answers,
    extract_campaign_inputs,
    finalize_campaign,
)
from app.creative import generate_creatives, render_ads, validate_creatives
from app.graph import _apply_answers, get_graph
from app.personas import catalog_personas, prefer_matches
from app.schemas import AdvertiserProfile, CampaignInputs, PersonaMatch, PublisherContext


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
    campaign_text: str = ""
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


def _snapshot(state: dict, *, personas: list[dict] | None = None, ads: list[dict] | None = None) -> dict:
    profile = state.get("profile")
    inputs = state.get("campaign_inputs")
    config = state.get("campaign_config")
    return {
        "profile": profile.model_dump() if profile else {},
        "contexts": [ctx.model_dump() for ctx in (state.get("publisher_contexts") or [])],
        "matches": [row.model_dump() for row in (state.get("persona_matches") or [])],
        "chosen": state.get("chosen") or [],
        "personas": personas if personas is not None else [row.to_public() for row in (state.get("persona_matches") or [])],
        "creatives": ads if ads is not None else [row.to_public() for row in (state.get("creatives") or [])],
        "campaign_inputs": inputs.model_dump() if inputs else {},
        "raw_query": state.get("raw_query") or "",
        "campaign": config.to_public() if config else None,
    }


def _to_result(state: dict) -> AgentResult:
    meta = state.get("question_meta")
    public = meta.to_public() if meta else None
    if state.get("status") == "insufficient_signal" and public:
        return AgentResult(question=public["question"], question_meta=public)
    ads = [row.to_public() for row in (state.get("creatives") or [])]
    personas = [row.to_public() for row in (state.get("persona_matches") or [])]
    campaign_question = bool(
        public
        and public.get("field")
        in {
            "campaign_objective",
            "total_budget_usd",
            "campaign_duration",
            "performance_goal",
        }
    )
    useful = bool(public and public.get("importance") == "useful")
    return AgentResult(
        text=state.get("text") or "",
        chosen=state.get("chosen") or [],
        question_meta=public if useful or campaign_question else None,
        personas=personas,
        creatives=ads,
        publishers_text=state.get("publishers_text") or "",
        personas_text=state.get("personas_text") or "",
        ads_text=state.get("ads_text") or "",
        campaign_text=state.get("campaign_text") or "",
        snapshot=_snapshot(state, personas=personas, ads=ads),
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
    picked = " ".join(
        (ans.get("value") or "") for ans in (answers or []) if ans.get("field") == "target_audience"
    ).lower()
    matches = prefer_matches(picked, matches)
    variants = validate_creatives(
        generate_creatives(profile, contexts, matches, catalog_personas()),
        profile,
    )
    ads_text = render_ads(variants, matches)
    creatives = [row.to_public() for row in variants]
    return AgentResult(
        text=ads_text,
        chosen=snapshot.get("chosen") or [],
        personas=snapshot.get("personas") or [],
        creatives=creatives,
        ads_text=ads_text,
        snapshot={
            **snapshot,
            "profile": profile.model_dump(),
            "matches": [row.model_dump() for row in matches],
            "personas": [row.to_public() for row in matches],
            "creatives": creatives,
        },
    )


def run_campaign(
    snapshot: dict,
    *,
    answers: list[dict] | None = None,
    skipped_fields: list[str] | None = None,
    asked_fields: list[str] | None = None,
    raw_update: str | None = None,
) -> AgentResult:
    """Resume after ads or a campaign question: campaign only, no re-rank."""
    profile = AdvertiserProfile.model_validate(snapshot["profile"])
    contexts = [PublisherContext.model_validate(row) for row in snapshot.get("contexts") or []]
    matches = [PersonaMatch.model_validate(row) for row in snapshot.get("matches") or []]
    # The query only seeds what the snapshot has not resolved yet, otherwise a later
    # revision would be overwritten by the "30 days" still sitting in the first message.
    inputs = extract_campaign_inputs(snapshot.get("raw_query") or "").merge(
        CampaignInputs.model_validate(snapshot.get("campaign_inputs") or {})
    )
    inputs = apply_campaign_answers(inputs, answers)
    # A revision is newer than the answers that built the current plan, so it wins.
    if raw_update:
        inputs = inputs.merge(extract_campaign_inputs(raw_update))
    skipped = skipped_fields or []
    asked = asked_fields or []
    question = analyze_campaign_missing(
        inputs,
        skipped_fields=skipped,
        asked_fields=asked,
    )
    snap = {
        **snapshot,
        "campaign_inputs": inputs.model_dump(),
        "profile": profile.model_dump(),
        "matches": [row.model_dump() for row in matches],
    }
    if question:
        return AgentResult(
            chosen=snapshot.get("chosen") or [],
            personas=snapshot.get("personas") or [],
            creatives=snapshot.get("creatives") or [],
            question_meta=question.to_public(),
            snapshot=snap,
        )
    config, campaign_text = finalize_campaign(
        profile,
        inputs,
        snapshot.get("chosen") or [],
        contexts,
        matches,
        catalog_personas(),
        skipped_fields=skipped,
    )
    snap["campaign"] = config.to_public()
    snap["campaign_inputs"] = inputs.model_dump()
    return AgentResult(
        text=campaign_text,
        chosen=snapshot.get("chosen") or [],
        personas=snapshot.get("personas") or [],
        creatives=snapshot.get("creatives") or [],
        campaign_text=campaign_text,
        snapshot=snap,
    )
