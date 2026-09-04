"""LangGraph: required missing first, then rank ║ personas, then ads, then campaign."""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.campaign import (
    analyze_campaign_missing,
    apply_campaign_answers,
    build_campaign_config,
    explain_campaign,
    extract_campaign_inputs,
    render_campaign_text,
)
from app.creative import generate_creatives, render_ads, validate_creatives
from app.data import load_publishers
from app.llm import llm_enabled
from app.missing import analyze_missing, audience_question, is_escape_product
from app.personas import catalog_personas, match_personas, render_personas
from app.ranking import apply_constraints, score_publishers, select_recommendations
from app.reason import reason_about_matches, render_text
from app.retrieval import HashEmbedder, InMemoryPublisherRetriever, OpenAIEmbedder, PublisherRetriever
from app.schemas import (
    AdvertiserProfile,
    AudienceHint,
    CampaignConfig,
    CampaignInputs,
    CreativeVariant,
    ExclusionStats,
    MissingQuestion,
    PersonaMatch,
    Publisher,
    PublisherContext,
    Recommendation,
    ScoredPublisher,
)
from app.understand import extract_profile


class GraphState(TypedDict, total=False):
    raw_query: str
    clarification: str | None
    answers: list[dict]
    skipped_fields: list[str]
    asked_fields: list[str]
    profile: AdvertiserProfile
    scored: list[ScoredPublisher]
    recommendations: list[Recommendation]
    near_misses: list[ScoredPublisher]
    exclusions: ExclusionStats
    missing_question: MissingQuestion | None
    persona_matches: list[PersonaMatch]
    publisher_contexts: list[PublisherContext]
    creatives: list[CreativeVariant]
    publishers_text: str
    personas_text: str
    ads_text: str
    campaign_inputs: CampaignInputs
    campaign_config: CampaignConfig
    campaign_text: str
    status: str
    question_meta: MissingQuestion | None
    text: str
    chosen: list[dict]


def catalog() -> list[Publisher]:
    return [Publisher.from_raw(row) for row in load_publishers()]


def default_retriever() -> InMemoryPublisherRetriever:
    embedder = OpenAIEmbedder() if llm_enabled() else HashEmbedder()
    return InMemoryPublisherRetriever(catalog(), embedder=embedder)


def _apply_answers(profile: AdvertiserProfile, answers: list[dict] | None) -> AdvertiserProfile:
    data = profile.model_copy(deep=True)
    for ans in answers or []:
        field = ans.get("field") or ""
        value = (ans.get("value") or "").strip()
        if not value:
            continue
        if field == "product":
            if is_escape_product(value):
                continue
            data.product = data.product or value
            data.confidence = max(data.confidence, 0.7)
        elif field == "target_audience":
            aud = data.audience or AudienceHint()
            if any(ch.isdigit() for ch in value):
                aud.age_range = value
            elif "broad" in value.lower():
                if "broad audience" not in aud.interests:
                    aud.interests = [*aud.interests, "broad audience"]
            elif value not in aud.interests:
                aud.interests = [*aud.interests, value]
            data.audience = aud
    return data


def _is_required(state: GraphState) -> bool:
    missing = state.get("missing_question")
    answered = bool(state.get("clarification") or state.get("answers"))
    profile = state.get("profile")
    if missing and missing.importance == "required" and not answered:
        return True
    if profile and not profile.category and (not profile.product or is_escape_product(profile.product)):
        return True
    return False


def build_graph(retriever: PublisherRetriever | None = None):
    engine = retriever or default_retriever()
    personas = catalog_personas()

    def parse_advertiser(state: GraphState) -> dict:
        query = state["raw_query"].strip()
        bits: list[str] = []
        clarification = (state.get("clarification") or "").strip()
        if clarification:
            bits.append(f"Clarification: {clarification}")
        for ans in state.get("answers") or []:
            value = (ans.get("value") or "").strip()
            if value:
                bits.append(f"{ans.get('field') or 'note'}: {value}")
        if bits:
            query = f"{query}\n\n" + "\n".join(bits)
        profile = _apply_answers(extract_profile(query), state.get("answers"))
        if is_escape_product(profile.product or ""):
            profile = profile.model_copy(update={"product": None})
        return {"profile": profile}

    def analyze_missing_node(state: GraphState) -> dict:
        return {
            "missing_question": analyze_missing(
                state["profile"],
                skipped_fields=state.get("skipped_fields") or [],
                asked_fields=state.get("asked_fields") or [],
            )
        }

    def after_missing(state: GraphState) -> str:
        return "halt_required" if _is_required(state) else "ready_to_place"

    def halt_required(state: GraphState) -> dict:
        missing = state.get("missing_question")
        return {
            "status": "insufficient_signal",
            "question_meta": missing,
            "recommendations": [],
            "chosen": [],
            "text": "",
            "publishers_text": "",
            "personas_text": "",
            "ads_text": "",
            "persona_matches": [],
            "creatives": [],
        }

    def ready_to_place(state: GraphState) -> dict:
        return {}

    def rank_publishers(state: GraphState) -> dict:
        ranked = engine.retrieve_all(state["profile"])
        pool = ranked[: engine.pool_size]
        scored = apply_constraints(score_publishers(state["profile"], pool))
        recs, near, exclusions = select_recommendations(scored, ranked[engine.pool_size :])
        return {
            "scored": scored,
            "recommendations": recs,
            "near_misses": near,
            "exclusions": exclusions,
        }

    def match_personas_node(state: GraphState) -> dict:
        return {"persona_matches": match_personas(state["profile"], personas)}

    def assemble_result(state: GraphState) -> dict:
        profile = state["profile"]
        recs = state.get("recommendations") or []
        matches = state.get("persona_matches") or []
        if not recs:
            product = profile.product or "this product"
            publishers_text = (
                f"This catalog doesn’t have a place to run ads for {product}. "
                "The networks here are grocery, apparel, pet, home, and wellness — not this shelf."
            )
            return {
                "status": "ok",
                "publishers_text": publishers_text,
                "personas_text": "",
                "text": publishers_text,
                "question_meta": None,
                "publisher_contexts": [],
                "recommendations": [],
                "chosen": [],
                "persona_matches": [],
                "creatives": [],
            }
        reasoning = reason_about_matches(
            profile,
            recs,
            state.get("near_misses") or [],
            state.get("exclusions") or ExclusionStats(),
            "ok",
        )
        product = profile.product
        placed = any(rec.match_strength in {"strong", "moderate"} for rec in recs)
        if placed and product:
            lead = f"Here’s where I’d start for {product}."
        elif product:
            lead = (
                f"This catalog doesn’t have a natural shelf for {product}. "
                "Closest I can get:"
            )
        else:
            lead = "Closest I can get in this catalog:"
        body = render_text(profile, recs, reasoning, "ok")
        publishers_text = "\n".join(part for part in (lead, body) if part)
        personas_text = render_personas(matches)
        scored = state.get("scored") or []
        by_id = {row.publisher.id: row.publisher for row in scored}
        contexts = [
            PublisherContext.from_publisher(by_id[rec.publisher_id])
            for rec in recs
            if rec.publisher_id in by_id
        ]
        text = "\n".join(part for part in (publishers_text, personas_text) if part)
        return {
            "status": "ok",
            "publishers_text": publishers_text,
            "personas_text": personas_text,
            "text": text,
            "question_meta": None,
            "publisher_contexts": contexts,
            "recommendations": recs,
            "chosen": [rec.to_public() for rec in recs],
        }

    def after_assemble(state: GraphState) -> str:
        if not state.get("recommendations"):
            return END
        return "creative_generation"

    def creative_generation(state: GraphState) -> dict:
        return {
            "creatives": generate_creatives(
                state["profile"],
                state.get("publisher_contexts") or [],
                state.get("persona_matches") or [],
                personas,
            )
        }

    def validate_creatives_node(state: GraphState) -> dict:
        variants = validate_creatives(state.get("creatives") or [], state["profile"])
        ads_text = render_ads(variants, state.get("persona_matches") or [])
        base = state.get("text") or ""
        text = f"{base}\n{ads_text}" if ads_text and base else (ads_text or base)
        followup = audience_question(
            state["profile"],
            state.get("persona_matches") or [],
            skipped_fields=state.get("skipped_fields") or [],
            asked_fields=state.get("asked_fields") or [],
        )
        return {"creatives": variants, "ads_text": ads_text, "text": text, "question_meta": followup}

    def after_creatives(state: GraphState) -> str:
        return END if state.get("question_meta") else "campaign_input_analysis"

    def campaign_input_analysis(state: GraphState) -> dict:
        prior = state.get("campaign_inputs") or CampaignInputs()
        extracted = extract_campaign_inputs(state.get("raw_query") or "")
        inputs = apply_campaign_answers(prior.merge(extracted), state.get("answers"))
        question = analyze_campaign_missing(
            inputs,
            skipped_fields=state.get("skipped_fields") or [],
            asked_fields=state.get("asked_fields") or [],
        )
        return {"campaign_inputs": inputs, "question_meta": question}

    def after_campaign_inputs(state: GraphState) -> str:
        return END if state.get("question_meta") else "build_campaign"

    def build_campaign(state: GraphState) -> dict:
        inputs = state.get("campaign_inputs") or CampaignInputs()
        config = build_campaign_config(
            state["profile"],
            inputs,
            state.get("chosen") or [],
            state.get("publisher_contexts") or [],
            state.get("persona_matches") or [],
            personas,
            skipped_fields=state.get("skipped_fields") or [],
        )
        return {"campaign_config": config}

    def campaign_llm_strategist(state: GraphState) -> dict:
        config = state["campaign_config"]
        text = render_campaign_text(
            config,
            explain_campaign(config, state["profile"], state.get("persona_matches") or []),
        )
        return {"campaign_text": text}

    workflow = StateGraph(GraphState)
    workflow.add_node("parse_advertiser", parse_advertiser)
    workflow.add_node("analyze_missing", analyze_missing_node)
    workflow.add_node("halt_required", halt_required)
    workflow.add_node("ready_to_place", ready_to_place)
    workflow.add_node("rank_publishers", rank_publishers)
    workflow.add_node("match_personas", match_personas_node)
    workflow.add_node("assemble_result", assemble_result)
    workflow.add_node("creative_generation", creative_generation)
    workflow.add_node("validate_creatives", validate_creatives_node)
    workflow.add_node("campaign_input_analysis", campaign_input_analysis)
    workflow.add_node("build_campaign", build_campaign)
    workflow.add_node("campaign_llm_strategist", campaign_llm_strategist)
    workflow.add_edge(START, "parse_advertiser")
    workflow.add_edge("parse_advertiser", "analyze_missing")
    workflow.add_conditional_edges(
        "analyze_missing",
        after_missing,
        {"halt_required": "halt_required", "ready_to_place": "ready_to_place"},
    )
    workflow.add_edge("halt_required", END)
    # Official fan-out: multiple outgoing edges run in one superstep.
    workflow.add_edge("ready_to_place", "rank_publishers")
    workflow.add_edge("ready_to_place", "match_personas")
    workflow.add_edge("rank_publishers", "assemble_result")
    workflow.add_edge("match_personas", "assemble_result")
    workflow.add_conditional_edges(
        "assemble_result",
        after_assemble,
        {"creative_generation": "creative_generation", END: END},
    )
    workflow.add_edge("creative_generation", "validate_creatives")
    workflow.add_conditional_edges(
        "validate_creatives",
        after_creatives,
        {"campaign_input_analysis": "campaign_input_analysis", END: END},
    )
    workflow.add_conditional_edges(
        "campaign_input_analysis",
        after_campaign_inputs,
        {"build_campaign": "build_campaign", END: END},
    )
    workflow.add_edge("build_campaign", "campaign_llm_strategist")
    workflow.add_edge("campaign_llm_strategist", END)
    return workflow.compile()


_GRAPH = None


def get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH


def reset_graph() -> None:
    global _GRAPH
    _GRAPH = None
