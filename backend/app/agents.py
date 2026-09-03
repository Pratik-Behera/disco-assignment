"""Phase 1 entry: understand → retrieve → rank → reason."""

from __future__ import annotations

from dataclasses import dataclass

from app.graph import get_graph


@dataclass
class AgentResult:
    """Either ask one clarify question or return the final reply."""

    text: str | None = None
    question: str | None = None
    chosen: list[dict] | None = None


def run(user_text: str, *, clarification: str | None = None) -> AgentResult:
    state = get_graph().invoke({"raw_query": user_text, "clarification": clarification})
    if (
        state.get("status") == "insufficient_signal"
        and not clarification
        and state.get("question")
    ):
        return AgentResult(question=state["question"])
    return AgentResult(text=state.get("text") or "", chosen=state.get("chosen") or [])
