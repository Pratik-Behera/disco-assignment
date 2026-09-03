"""
Agent logic lives here.

Flow you probably want:
  1. Understand the advertiser query (clarify if too vague).
  2. Filter publishers from catalog (Python — don't let the LLM invent matches).
  3. Reasoner picks from filtered list or says no fit.

Replace `run()` below; main.py only streams whatever this returns.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AgentResult:
    """Either ask one clarify question or return the final reply."""

    text: str | None = None
    question: str | None = None
    chosen: list[dict] | None = None


def run(user_text: str, *, clarification: str | None = None) -> AgentResult:
    query = user_text.strip()
    if clarification:
        query = f"{query}\n\nClarification: {clarification.strip()}"

    # ponytail: placeholder until you wire orchestrator + reasoner
    if clarification is None and len(query.split()) < 5:
        return AgentResult(
            question="What product or product family are you advertising?"
        )

    return AgentResult(
        text="Backend reset — implement matching in app/agents.py → run().",
        chosen=[],
    )
