"""Classify missing advertiser facts. Does not invent values or re-rank."""

from __future__ import annotations

import logging
import re

from app.data import load_publishers
from app.llm import llm_enabled, load_prompt, parse_structured
from app.schemas import AdvertiserProfile, MissingQuestion, PersonaMatch
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

_AUDIENCE_MARK = re.compile(
    r"\b(senior|women|woman|men|kids|parent|owners?|gifts?|gifting|health-?conscious)\b",
    re.I,
)


class _LlmMissing(BaseModel):
    field: str
    importance: str
    question: str
    quick_replies: list[str] = Field(default_factory=list)


def analyze_missing(
    profile: AdvertiserProfile,
    *,
    skipped_fields: list[str] | None = None,
    asked_fields: list[str] | None = None,
) -> MissingQuestion | None:
    """Required product gap only. Audience questions come from audience_question()."""
    skipped = set(skipped_fields or [])
    asked = set(asked_fields or [])
    heuristic = _required_heuristic(profile, skipped, asked)
    if not llm_enabled():
        return heuristic
    if heuristic is None:
        return None
    try:
        parsed = parse_structured(
            load_prompt("missing_information.md"),
            profile.model_dump_json(indent=2),
            _LlmMissing,
        )
        if parsed.importance != "required" or parsed.field in skipped or parsed.field in asked:
            return heuristic
        replies = parsed.quick_replies or heuristic.quick_replies
        return MissingQuestion(
            field=parsed.field or "product",
            importance="required",
            question=parsed.question.strip() or heuristic.question,
            quick_replies=replies,
            allow_free_text=True,
            allow_skip=False,
        )
    except Exception:
        log.warning("LLM missing-info failed; falling back to heuristic", exc_info=True)
        return heuristic


def audience_question(
    profile: AdvertiserProfile,
    matches: list[PersonaMatch],
    *,
    skipped_fields: list[str] | None = None,
    asked_fields: list[str] | None = None,
) -> MissingQuestion | None:
    """Useful shopper pick, after personas exist. Chips are matched persona names."""
    skipped = set(skipped_fields or [])
    asked = set(asked_fields or [])
    if "target_audience" in skipped or "target_audience" in asked:
        return None
    if _has_audience_signal(profile):
        return None
    if not profile.product:
        return None
    replies = []
    preferred = [row for row in matches if "category overlap" in row.match_signals]
    for row in preferred or matches:
        label = row.persona_name.removeprefix("The ").strip()
        if label and label not in replies:
            replies.append(label)
        if len(replies) >= 4:
            break
    if not replies:
        replies = ["Broad audience"]
    return MissingQuestion(
        field="target_audience",
        importance="useful",
        question="Which shoppers should the ads speak to first?",
        quick_replies=replies,
        allow_free_text=True,
        allow_skip=True,
    )


def _has_audience_signal(profile: AdvertiserProfile) -> bool:
    aud = profile.audience
    if aud and (aud.age_range or aud.gender or aud.income or aud.interests):
        return True
    blob = " ".join(
        [
            *(profile.product_attributes or []),
            *(profile.keywords or []),
            profile.product or "",
            profile.raw_query,
        ]
    )
    return bool(_AUDIENCE_MARK.search(blob))


def _required_heuristic(
    profile: AdvertiserProfile,
    skipped: set[str],
    asked: set[str],
) -> MissingQuestion | None:
    if "product" in skipped or "product" in asked:
        return None
    if profile.product or profile.category:
        return None
    return MissingQuestion(
        field="product",
        importance="required",
        question="What product or product family are you advertising?",
        quick_replies=_product_replies(),
        allow_free_text=True,
        allow_skip=False,
    )


def _product_replies() -> list[str]:
    labels = {
        "pet": "Pet products",
        "apparel": "Apparel",
        "drinks": "Drinks",
        "grocery": "Food & grocery",
        "home": "Home",
        "wellness_dtc": "Wellness",
        "beauty": "Beauty",
        "health": "Health",
    }
    seen: list[str] = []
    for row in load_publishers():
        label = labels.get(row.get("category", ""), "")
        if label and label not in seen:
            seen.append(label)
        if len(seen) >= 4:
            break
    return seen or ["Pet products", "Apparel", "Food & drink", "Home & wellness"]
