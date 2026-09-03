"""LangGraph: understand → retrieve → rank → reason. Ranking stays in Python."""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.data import load_publishers
from app.llm import llm_enabled
from app.ranking import (
    apply_constraints,
    insufficient_signal,
    score_publishers,
    select_recommendations,
)
from app.reason import reason_about_matches, render_text
from app.retrieval import HashEmbedder, InMemoryPublisherRetriever, OpenAIEmbedder, PublisherRetriever
from app.schemas import (
    AdvertiserProfile,
    ExclusionStats,
    Publisher,
    PublisherCandidate,
    Recommendation,
    ScoredPublisher,
)
from app.understand import clarification_question, extract_profile, validate_profile


class GraphState(TypedDict, total=False):
    raw_query: str
    clarification: str | None
    profile: AdvertiserProfile
    candidates: list[PublisherCandidate]
    rejected: list[PublisherCandidate]
    scored: list[ScoredPublisher]
    recommendations: list[Recommendation]
    near_misses: list[ScoredPublisher]
    exclusions: ExclusionStats
    status: str
    question: str | None
    text: str
    chosen: list[dict]


def catalog() -> list[Publisher]:
    return [Publisher.from_raw(row) for row in load_publishers()]


def default_retriever() -> InMemoryPublisherRetriever:
    embedder = OpenAIEmbedder() if llm_enabled() else HashEmbedder()
    return InMemoryPublisherRetriever(catalog(), embedder=embedder)


def build_graph(retriever: PublisherRetriever | None = None):
    engine = retriever or default_retriever()

    def parse_advertiser(state: GraphState) -> dict:
        query = state["raw_query"].strip()
        clarification = (state.get("clarification") or "").strip()
        if clarification:
            query = f"{query}\n\nClarification: {clarification}"
        return {"profile": extract_profile(query)}

    def validate_profile_node(state: GraphState) -> dict:
        profile = validate_profile(state["profile"])
        already = bool(state.get("clarification"))
        status = "insufficient_signal" if insufficient_signal(profile, already_clarified=already) else "ok"
        question = clarification_question(profile) if status == "insufficient_signal" else None
        return {"profile": profile, "status": status, "question": question}

    def retrieve_publishers(state: GraphState) -> dict:
        ranked = engine.retrieve_all(state["profile"])
        pool = ranked[: engine.pool_size]
        return {"candidates": pool, "rejected": ranked[engine.pool_size :]}

    def score_publishers_node(state: GraphState) -> dict:
        return {"scored": score_publishers(state["profile"], state["candidates"])}

    def apply_constraints_node(state: GraphState) -> dict:
        return {"scored": apply_constraints(state["scored"])}

    def select_recommendations_node(state: GraphState) -> dict:
        recs, near, exclusions = select_recommendations(state["scored"], state.get("rejected") or [])
        if state.get("status") == "insufficient_signal":
            recs = []
        return {"recommendations": recs, "near_misses": near, "exclusions": exclusions}

    def reason_about_matches_node(state: GraphState) -> dict:
        status = state.get("status") or "ok"
        recs = state.get("recommendations") or []
        reasoning = reason_about_matches(
            state["profile"],
            recs,
            state.get("near_misses") or [],
            state.get("exclusions") or ExclusionStats(),
            status,
        )
        text = render_text(state["profile"], recs, reasoning, status)
        return {
            "text": text,
            "question": reasoning.clarification or state.get("question"),
            "chosen": [rec.to_public() for rec in recs],
        }

    workflow = StateGraph(GraphState)
    workflow.add_node("parse_advertiser", parse_advertiser)
    workflow.add_node("validate_profile", validate_profile_node)
    workflow.add_node("retrieve_publishers", retrieve_publishers)
    workflow.add_node("score_publishers", score_publishers_node)
    workflow.add_node("apply_constraints", apply_constraints_node)
    workflow.add_node("select_recommendations", select_recommendations_node)
    workflow.add_node("reason_about_matches", reason_about_matches_node)
    workflow.add_edge(START, "parse_advertiser")
    workflow.add_edge("parse_advertiser", "validate_profile")
    workflow.add_edge("validate_profile", "retrieve_publishers")
    workflow.add_edge("retrieve_publishers", "score_publishers")
    workflow.add_edge("score_publishers", "apply_constraints")
    workflow.add_edge("apply_constraints", "select_recommendations")
    workflow.add_edge("select_recommendations", "reason_about_matches")
    workflow.add_edge("reason_about_matches", END)
    return workflow.compile()


_GRAPH = None


def get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH
