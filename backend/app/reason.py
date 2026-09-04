"""Turn ranked evidence into advertiser-facing copy. Does not re-rank."""

from __future__ import annotations

import json
import logging

from app.llm import llm_enabled, load_prompt, parse_structured
from app.schemas import (
    AdvertiserProfile,
    ExclusionStats,
    NearMissReason,
    PublisherReason,
    ReasoningResult,
    Recommendation,
    ScoredPublisher,
)

log = logging.getLogger(__name__)


def _strength_label(rec: Recommendation) -> str:
    if rec.match_strength == "strong":
        return "Strongest available fit" if rec.score >= 0.75 else "Strong fit"
    if rec.match_strength == "moderate":
        return "Solid adjacent fit"
    return "Weak catalog match"


def reason_heuristic(
    profile: AdvertiserProfile,
    recommendations: list[Recommendation],
    near_misses: list[ScoredPublisher],
    exclusions: ExclusionStats,
    status: str,
) -> ReasoningResult:
    if status == "insufficient_signal":
        understood = profile.product or profile.category or "no specific product"
        missing = "; ".join(profile.ambiguities) or "the product being advertised"
        return ReasoningResult(
            recommendations=[],
            near_misses=[],
            remainder=(
                "I don't have enough information to confidently recommend publishers. "
                f"Understood: {understood}. Missing: {missing}."
            ),
            clarification="What product or product family are you advertising?",
        )
    if not recommendations:
        product = profile.product or "this advertiser"
        return ReasoningResult(
            remainder=(
                f"I don't have a confident publisher fit for {product} in this catalog. "
                f"{exclusions.remainder}"
            )
        )

    recs = []
    for rec in recommendations:
        caveat = (
            rec.evidence.product_match
            if rec.evidence.product_match.startswith("No assortment")
            else ""
        )
        recs.append(
            PublisherReason(
                publisher_id=rec.publisher_id,
                headline=f"{rec.publisher_name} — {_strength_label(rec)}",
                why=rec.evidence.category_match,
                caveat=caveat,
            )
        )
    misses = [
        NearMissReason(
            publisher_id=row.publisher.id,
            publisher_name=row.publisher.name,
            explanation=(
                f"{row.publisher.name} — close on the {row.publisher.category} shelf, "
                "but not a stronger fit than the names above."
            ),
        )
        for row in near_misses
    ]
    return ReasoningResult(
        recommendations=recs,
        near_misses=misses,
        remainder=exclusions.remainder,
    )


def reason_about_matches(
    profile: AdvertiserProfile,
    recommendations: list[Recommendation],
    near_misses: list[ScoredPublisher],
    exclusions: ExclusionStats,
    status: str,
) -> ReasoningResult:
    fallback = reason_heuristic(profile, recommendations, near_misses, exclusions, status)
    if not llm_enabled():
        return fallback
    payload = {
        "status": status,
        "advertiser": profile.model_dump(),
        "recommendations": [
            {
                **rec.to_public(),
                "headline_hint": _strength_label(rec),
            }
            for rec in recommendations
        ],
        "near_misses": [
            {
                "publisher_id": row.publisher.id,
                "publisher_name": row.publisher.name,
                "score": round(row.score, 2),
                "confidence": round(row.confidence, 2),
                "evidence": row.evidence.model_dump(),
                "penalty_reasons": row.features.penalty_reasons,
            }
            for row in near_misses
        ],
        "exclusions": exclusions.model_dump(),
        "remainder": exclusions.remainder,
    }
    try:
        result = parse_structured(
            load_prompt("publisher_reasoning.md"),
            json.dumps(payload, indent=2),
            ReasoningResult,
        )
        result.remainder = (result.remainder or "").strip() or fallback.remainder
        return result
    except Exception:
        log.warning("LLM reasoning failed; falling back to heuristic copy", exc_info=True)
        return fallback


def _one_line(text: str) -> str:
    return " ".join(text.split())


def render_text(
    profile: AdvertiserProfile,
    recommendations: list[Recommendation],
    reasoning: ReasoningResult,
    status: str,
) -> str:
    if status == "insufficient_signal":
        parts = [_one_line(reasoning.remainder)]
        if reasoning.clarification:
            parts.append(_one_line(reasoning.clarification))
        return "\n".join(p for p in parts if p)

    lines: list[str] = []
    reasons = {item.publisher_id: item for item in reasoning.recommendations}
    for rec in recommendations:
        item = reasons.get(rec.publisher_id)
        headline = item.headline if item else f"{rec.publisher_name} — {_strength_label(rec)}"
        if " — " in headline and not headline.startswith("**"):
            name, _, rest = headline.partition(" — ")
            headline = f"**{name}** — {rest}"
        why = _one_line(item.why if item else rec.evidence.category_match)
        caveat = _one_line(item.caveat) if item and item.caveat else ""
        if caveat.startswith("No advertiser audience"):
            caveat = ""
        lines.append(headline)
        lines.append(f"• {why}")
        if caveat:
            lines.append(f"• Caveat: {caveat}")

    if reasoning.near_misses:
        lines.append("**Near misses**")
        for miss in reasoning.near_misses:
            expl = _one_line(miss.explanation)
            if expl.lower().startswith(miss.publisher_name.lower()):
                lines.append(f"• {expl}")
            else:
                lines.append(f"• {miss.publisher_name} — {expl}")

    if reasoning.remainder:
        lines.append(_one_line(reasoning.remainder))
    text = "\n".join(lines).strip()
    if text:
        return text
    return (
        "No publisher in this catalog is a confident fit for "
        f"{profile.product or 'this advertiser'}."
    )
