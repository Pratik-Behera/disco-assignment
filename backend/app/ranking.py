"""Deterministic publisher ranking. Initial heuristic weights, not learned.

Score = how strong the available evidence looks.
Confidence = how trustworthy / complete that evidence is.
"""

from __future__ import annotations

import re
from typing import Iterable

from app.retrieval import keyword_match, parts, structured_matches
from app.schemas import (
    AdvertiserProfile,
    ExclusionStats,
    FeatureEvidence,
    FeatureScores,
    MatchStrength,
    Publisher,
    PublisherCandidate,
    Recommendation,
    ScoredPublisher,
)

# Initial heuristics. Recalibrate when campaign outcome data exists.
CATEGORY_WEIGHT = 0.20
SUBCATEGORY_WEIGHT = 0.16
PRODUCT_WEIGHT = 0.14
KEYWORD_WEIGHT = 0.10
SEMANTIC_WEIGHT = 0.10
AUDIENCE_WEIGHT = 0.10
ECONOMIC_WEIGHT = 0.10
BEHAVIOR_WEIGHT = 0.06
BUSINESS_MODEL_WEIGHT = 0.04

TOP_N = 4
NEAR_MISS_MAX = 3
MIN_RECOMMEND_SCORE = 0.34
NEAR_MISS_FLOOR = 0.22
OUT_OF_TOPIC = 0.12
INSUFFICIENT_PROFILE_CONFIDENCE = 0.45

_AGE = re.compile(r"(\d{2})")
_AOV_BANDS = {
    "budget": (15.0, 55.0),
    "mid": (40.0, 110.0),
    "premium": (80.0, 200.0),
    "luxury": (150.0, 2000.0),
}


def _clip(value: float) -> float:
    return max(0.0, min(1.0, value))


def _age_bounds(text: str) -> tuple[int, int] | None:
    nums = [int(n) for n in _AGE.findall(text)]
    if not nums:
        return None
    return min(nums), max(nums)


def _age_overlap(advertiser: str, publisher: str) -> float | None:
    a = _age_bounds(advertiser)
    b = _age_bounds(publisher)
    if not a or not b:
        return None
    lo, hi = max(a[0], b[0]), min(a[1], b[1])
    if hi < lo:
        return 0.15
    span = max(a[1] - a[0], b[1] - b[0], 1)
    return _clip(0.4 + 0.6 * ((hi - lo) / span))


def audience_fit(profile: AdvertiserProfile, publisher: Publisher) -> tuple[float, bool]:
    """Neutral 0.5 when the advertiser stated no audience. Contradiction is flagged."""
    hint = profile.audience
    if hint is None or not any([hint.age_range, hint.gender, hint.income, hint.interests]):
        return 0.5, False
    aud = publisher.audience
    scores: list[float] = []
    conflict = False
    if hint.age_range:
        overlap = _age_overlap(hint.age_range, aud.age_skew)
        if overlap is None:
            scores.append(0.5)
        else:
            scores.append(overlap)
            if overlap < 0.25:
                conflict = True
    if hint.gender:
        g = hint.gender.lower()
        split = aud.gender_split
        if "women" in g or g == "female":
            share = split.get("female", 0.5)
            scores.append(share)
            if share < 0.25:
                conflict = True
        elif "men" in g or g == "male":
            share = split.get("male", 0.5)
            scores.append(share)
            if share < 0.25:
                conflict = True
        else:
            scores.append(0.5)
    if hint.income and aud.income_tier:
        hi, ti = hint.income.lower(), aud.income_tier.lower()
        if hi in ti or ti in hi:
            scores.append(0.85)
        elif {"high", "luxury", "premium"} & set(hi.split()) and "mid" in ti and "high" not in ti:
            scores.append(0.35)
            conflict = True
        else:
            scores.append(0.55)
    if hint.interests:
        note_parts = parts(publisher.notes + " " + " ".join(publisher.subcategories))
        hits = sum(1 for interest in hint.interests if parts(interest) & note_parts)
        scores.append(hits / len(hint.interests) if hits else 0.4)
    return (sum(scores) / len(scores) if scores else 0.5), conflict


def economic_fit(profile: AdvertiserProfile, publisher: Publisher) -> float:
    if profile.price_position == "unknown":
        return 0.5
    lo, hi = _AOV_BANDS[profile.price_position]
    aov = publisher.avg_order_value_usd
    if lo <= aov <= hi:
        return 0.9
    if aov < lo:
        return _clip(0.85 * (aov / lo))
    return _clip(0.85 * (hi / aov) + 0.1)


def behavioral_fit(profile: AdvertiserProfile, publisher: Publisher) -> float:
    note = publisher.notes.lower()
    blob = " ".join(
        [
            *(profile.keywords or []),
            *(profile.product_attributes or []),
            profile.product or "",
            profile.category or "",
            profile.business_model or "",
        ]
    ).lower()
    score = 0.45
    if profile.business_model == "subscription" and (
        "subscription" in publisher.subcategories or "subscription" in note
    ):
        score += 0.3
    if any(w in blob for w in ("impulse", "late night", "beverage", "drink", "alcohol", "whisky")):
        if "late-night" in note or "impulse" in note or "high-frequency" in note:
            score += 0.2
    if any(w in blob for w in ("gift", "candle", "handbag")) and "gift" in note:
        score += 0.15
    if any(w in blob for w in ("sustainable", "refillable", "organic", "non_toxic")):
        if any(w in note for w in ("sustain", "clean-ingredient", "values", "non_toxic")):
            score += 0.15
        if "sustainable" in publisher.subcategories or "non_toxic" in publisher.subcategories:
            score += 0.1
    if any(w in blob for w in ("wellness", "supplement", "vitamin", "functional")):
        if any(w in note for w in ("wellness", "health", "science")):
            score += 0.15
    return _clip(score)


def business_model_fit(profile: AdvertiserProfile, publisher: Publisher) -> float:
    if not profile.business_model:
        return 0.5
    if profile.business_model == "subscription":
        if "subscription" in publisher.subcategories or "subscription" in publisher.notes.lower():
            return 0.9
        return 0.35
    return 0.5


def _structured_max(features: FeatureScores) -> float:
    return max(
        features.category_match,
        features.subcategory_match,
        features.product_match,
        features.keyword_match,
    )


def apply_penalties(features: FeatureScores, audience_conflict: bool) -> FeatureScores:
    penalty = 1.0
    reasons: list[str] = []
    structured = _structured_max(features)
    if structured < 0.12 and features.semantic_similarity < 0.35:
        penalty *= 0.30
        reasons.append("category_mismatch")
    if features.semantic_similarity >= 0.55 and structured < 0.18:
        penalty *= 0.50
        reasons.append("semantic_without_shelf")
    if audience_conflict:
        penalty *= 0.72
        reasons.append("audience_conflict")
    features.penalty = round(penalty, 4)
    features.penalty_reasons = reasons
    return features


def _weighted_score(features: FeatureScores) -> float:
    raw = (
        features.category_match * CATEGORY_WEIGHT
        + features.subcategory_match * SUBCATEGORY_WEIGHT
        + features.product_match * PRODUCT_WEIGHT
        + features.keyword_match * KEYWORD_WEIGHT
        + features.semantic_similarity * SEMANTIC_WEIGHT
        + features.audience_fit * AUDIENCE_WEIGHT
        + features.economic_fit * ECONOMIC_WEIGHT
        + features.behavioral_fit * BEHAVIOR_WEIGHT
        + features.business_model_fit * BUSINESS_MODEL_WEIGHT
    )
    if _structured_max(features) >= 0.85:
        raw += 0.12
    return _clip(raw * features.penalty)


def match_confidence(profile: AdvertiserProfile, features: FeatureScores) -> float:
    value = profile.confidence
    if not profile.product:
        value *= 0.55
    if not profile.category and not profile.subcategory:
        value *= 0.7
    elif profile.category and not profile.product:
        value *= 0.75
    structured = _structured_max(features)
    if structured < 0.25:
        value *= 0.65
    if features.semantic_similarity >= 0.55 and structured < 0.22:
        value *= 0.55
    if features.penalty_reasons:
        value *= 0.75
    if features.product_match < 0.5:
        value *= 0.80
    if "broad" in " ".join(profile.ambiguities).lower():
        value *= 0.85
    return round(_clip(value), 4)


def match_strength(score: float) -> MatchStrength:
    if score >= 0.62:
        return "strong"
    if score >= 0.42:
        return "moderate"
    return "weak"


def _evidence(profile: AdvertiserProfile, publisher: Publisher, features: FeatureScores) -> FeatureEvidence:
    shelf = f"{publisher.category} / {', '.join(publisher.subcategories)}"
    product = profile.product or "this product"
    if features.subcategory_match >= 0.8 or features.category_match >= 0.8:
        category = f"{publisher.name}'s catalog ({shelf}) directly overlaps the advertised category."
    elif max(features.category_match, features.subcategory_match, features.keyword_match) >= 0.4:
        category = f"Related shelf signal on {publisher.name} ({shelf}), not an exact product-line match."
    else:
        category = f"No meaningful category overlap with {publisher.name} ({shelf})."

    if features.product_match >= 0.75:
        product_note = f"{publisher.name} lists assortment terms that match {product}."
    else:
        product_note = f"No assortment or audience listed for {product}."

    hint = profile.audience
    if hint and (hint.age_range or hint.gender):
        audience = (
            f"Advertiser targeting ({hint.age_range or 'unspecified age'}, "
            f"{hint.gender or 'unspecified gender'}) vs publisher audience "
            f"{publisher.audience.age_skew}, {publisher.audience.income_tier}."
        )
    else:
        audience = (
            f"No advertiser audience was stated. Publisher audience is "
            f"{publisher.audience.age_skew}, {publisher.audience.income_tier} — not used as a positive claim."
        )

    if features.behavioral_fit >= 0.6:
        behavioral = f"Notes are directionally relevant: {publisher.notes}"
    else:
        behavioral = f"Limited behavioral overlap. Publisher notes: {publisher.notes}"
    return FeatureEvidence(
        category_match=category,
        product_match=product_note,
        audience_fit=audience,
        behavioral_fit=behavioral,
    )


def score_candidate(profile: AdvertiserProfile, candidate: PublisherCandidate) -> ScoredPublisher:
    publisher = candidate.publisher
    cat, sub, prod = structured_matches(profile, publisher)
    features = candidate.features.model_copy(deep=True)
    features.category_match = round(cat, 4)
    features.subcategory_match = round(sub, 4)
    features.product_match = round(prod, 4)
    features.keyword_match = round(keyword_match(profile, publisher), 4)
    aud, conflict = audience_fit(profile, publisher)
    features.audience_fit = round(aud, 4)
    features.economic_fit = round(economic_fit(profile, publisher), 4)
    features.behavioral_fit = round(behavioral_fit(profile, publisher), 4)
    features.business_model_fit = round(business_model_fit(profile, publisher), 4)
    apply_penalties(features, conflict)
    score = round(_weighted_score(features), 4)
    confidence = match_confidence(profile, features)
    return ScoredPublisher(
        publisher=publisher,
        score=score,
        confidence=confidence,
        match_strength=match_strength(score),
        features=features,
        evidence=_evidence(profile, publisher, features),
        eligible=score >= MIN_RECOMMEND_SCORE and "category_mismatch" not in features.penalty_reasons,
    )


def score_publishers(profile: AdvertiserProfile, candidates: Iterable[PublisherCandidate]) -> list[ScoredPublisher]:
    ranked = [score_candidate(profile, c) for c in candidates]
    ranked.sort(key=lambda row: (row.score, row.confidence), reverse=True)
    return ranked


def apply_constraints(scored: list[ScoredPublisher]) -> list[ScoredPublisher]:
    for row in scored:
        if row.features.penalty <= 0.4 and row.score < 0.5:
            row.eligible = False
    return scored


def _to_recommendation(row: ScoredPublisher) -> Recommendation:
    return Recommendation(
        publisher_id=row.publisher.id,
        publisher_name=row.publisher.name,
        score=row.score,
        confidence=row.confidence,
        match_strength=row.match_strength,
        evidence=row.evidence,
    )


def select_recommendations(
    scored: list[ScoredPublisher],
    rejected: list[PublisherCandidate],
    *,
    top_n: int = TOP_N,
) -> tuple[list[Recommendation], list[ScoredPublisher], ExclusionStats]:
    eligible = [row for row in scored if row.eligible]
    chosen_rows = eligible[:top_n]
    if not chosen_rows:
        # Catalog gap: keep the least-wrong adjacent rows, never a contradicted category.
        fallback = [
            row
            for row in scored
            if "category_mismatch" not in row.features.penalty_reasons
        ]
        chosen_rows = fallback[: min(2, top_n)]
        for row in chosen_rows:
            row.match_strength = "weak"
    chosen_ids = {row.publisher.id for row in chosen_rows}
    leftovers = [row for row in scored if row.publisher.id not in chosen_ids]
    near = [
        row
        for row in leftovers
        if row.score >= NEAR_MISS_FLOOR
        and "category_mismatch" not in row.features.penalty_reasons
        and _structured_max(row.features) >= 0.18
    ][:NEAR_MISS_MAX]
    near_ids = {row.publisher.id for row in near}

    out_of_topic = sum(1 for c in rejected if c.retrieval_score < OUT_OF_TOPIC)
    weak = sum(1 for c in rejected if c.retrieval_score >= OUT_OF_TOPIC)
    weak += sum(1 for row in leftovers if row.publisher.id not in near_ids)
    stats = ExclusionStats(
        out_of_topic=out_of_topic,
        weak_indirect=weak,
        near_miss=len(near),
        remainder=(
            f"I left the rest of the catalog out — {out_of_topic} are a different category, "
            f"{weak} are only a weak or indirect match."
        ),
    )
    return [_to_recommendation(row) for row in chosen_rows], near, stats


def insufficient_signal(profile: AdvertiserProfile, *, already_clarified: bool) -> bool:
    if already_clarified:
        return profile.confidence < 0.25 and not profile.product
    if profile.confidence < INSUFFICIENT_PROFILE_CONFIDENCE:
        return True
    return not profile.product and not profile.category
