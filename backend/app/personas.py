"""Score all shopper personas in Python. Unknown is not mismatch."""

from __future__ import annotations

import re

from app.data import load_personas
from app.retrieval import HashEmbedder, cosine_similarity, norm, tokenize
from app.schemas import AdvertiserProfile, PersonaMatch, ShopperPersona

# ponytail: initial weights, not learned. Drop a key to ignore that signal.
PERSONA_WEIGHTS = {
    "category_affinity": 0.42,
    "description": 0.22,
    "price": 0.12,
    "aov": 0.08,
    "age": 0.08,
    "gender": 0.04,
}

TOP_PERSONAS = 5
_GIFT_MARK = re.compile(r"\bgifts?\b|\bgifting\b", re.I)

# Chip / prose labels. Matching still keys on persona_id, never these strings.
PERSONA_SPEAK = {
    "persona_001": "People who treat health like a system",
    "persona_002": "Busy parents",
    "persona_003": "Younger shoppers who buy on look",
    "persona_004": "Pet owners who treat pets like family",
    "persona_005": "Shoppers who pay for things that last",
    "persona_006": "Shoppers who buy on footprint",
    "persona_007": "Busy shoppers who want it easy",
    "persona_008": "People hunting a better price",
    "persona_009": "Shoppers who buy for performance",
    "persona_010": "People shopping for a gift",
}

# Bridges advertiser words the persona file does not name (alcohol, whisky).
_RELATED_AFFINITY = {
    "alcohol": ("gourmet_food", "premium_grocery", "premium_basics"),
    "whisky": ("gourmet_food", "premium_grocery", "premium_basics"),
    "whiskey": ("gourmet_food", "premium_grocery", "premium_basics"),
    "spirits": ("gourmet_food", "premium_grocery", "premium_basics"),
}

_PRICE_TO_SENSITIVITY = {
    "budget": {"high", "medium-high"},
    "mid": {"medium", "medium-high", "high"},
    "premium": {"low", "low-medium", "medium"},
    "luxury": {"low", "low-medium"},
}


def catalog_personas() -> list[ShopperPersona]:
    return [ShopperPersona.from_raw(row) for row in load_personas()]


def match_personas(
    profile: AdvertiserProfile,
    personas: list[ShopperPersona] | None = None,
    *,
    weights: dict[str, float] | None = None,
    top_n: int = TOP_PERSONAS,
) -> list[PersonaMatch]:
    rows = personas or catalog_personas()
    w = weights or PERSONA_WEIGHTS
    embedder = HashEmbedder()
    query = " ".join(
        part
        for part in (
            profile.product,
            profile.category,
            profile.subcategory,
            " ".join(profile.keywords),
            " ".join(profile.product_attributes),
        )
        if part
    )
    q_vec = embedder.embed([query or profile.raw_query])[0]
    scored = [_score_one(profile, persona, q_vec, embedder, w) for persona in rows]
    scored.sort(key=lambda m: (-m.score, -m.confidence, m.persona_id))
    kept = [
        row
        for row in scored
        if "category overlap" in row.match_signals or "shopping for gifts" in row.match_signals
    ]
    return (kept or scored)[: min(top_n, 5)]


def _score_one(
    profile: AdvertiserProfile,
    persona: ShopperPersona,
    q_vec: list[float],
    embedder: HashEmbedder,
    weights: dict[str, float],
) -> PersonaMatch:
    parts: dict[str, float] = {}
    match_signals: list[str] = []
    negative: list[str] = []

    aff, hit_labels = _affinity(profile, persona)
    parts["category_affinity"] = aff
    if aff >= 0.5:
        match_signals.append("category overlap")
        if hit_labels:
            match_signals.append("fits " + ", ".join(hit_labels[:2]))

    desc = cosine_similarity(q_vec, embedder.embed([persona.description])[0])
    parts["description"] = desc
    if desc >= 0.25:
        match_signals.append("description fit")

    if profile.price_position != "unknown":
        liked = _PRICE_TO_SENSITIVITY.get(profile.price_position, set())
        parts["price"] = 0.85 if persona.price_sensitivity in liked else 0.25
        if parts["price"] >= 0.7:
            match_signals.append("price positioning")
        else:
            negative.append("price sensitivity clash")

    if profile.price_position in {"premium", "luxury"} and persona.typical_aov_usd:
        parts["aov"] = 0.8 if persona.typical_aov_usd >= 70 else 0.35
    elif profile.price_position == "budget" and persona.typical_aov_usd:
        parts["aov"] = 0.8 if persona.typical_aov_usd <= 70 else 0.35

    age = (profile.audience.age_range if profile.audience else None) or ""
    if age and persona.age_range:
        parts["age"] = _age_overlap(age, persona.age_range)
        if parts["age"] >= 0.5:
            match_signals.append("age overlap")

    gender = (profile.audience.gender if profile.audience else None) or ""
    if gender and persona.gender_skew:
        parts["gender"] = _gender_fit(gender, persona.gender_skew)

    used = {k: v for k, v in parts.items() if k in weights}
    wsum = sum(weights[k] for k in used) or 1.0
    score = sum(used[k] * weights[k] for k in used) / wsum

    for tag in persona.disinterested_in:
        if _mentions(profile, tag):
            score *= 0.55
            negative.append(f"dislikes {tag}")

    if _gift_intent(profile) and "gifter" in persona.name.lower():
        score = min(1.0, score + 0.28)
        match_signals.append("shopping for gifts")

    confidence = min(1.0, 0.35 + 0.12 * len(used))
    if not profile.product and not profile.category:
        confidence = min(confidence, 0.35)
        score = min(score, 0.4)
    return PersonaMatch(
        persona_id=persona.id,
        persona_name=persona.name,
        score=round(max(0.0, min(1.0, score)), 4),
        confidence=round(confidence, 4),
        match_signals=match_signals,
        negative_signals=negative,
    )


def _affinity(profile: AdvertiserProfile, persona: ShopperPersona) -> tuple[float, list[str]]:
    tags = {norm(profile.category or ""), norm(profile.subcategory or ""), norm(profile.product or "")}
    tags |= {norm(k) for k in profile.keywords}
    tags |= {norm(a) for a in profile.product_attributes}
    blob = " ".join(tags)
    for key, related in _RELATED_AFFINITY.items():
        if key in blob:
            tags |= {norm(item) for item in related}
    tags.discard("")
    if not tags:
        return 0.0, []
    labels: list[str] = []
    for aff in persona.category_affinities:
        n = norm(aff)
        if n in tags or any(n in t or t in n for t in tags if t):
            labels.append(aff.replace("_", " "))
    if not labels:
        return 0.05, []
    return min(1.0, 0.35 + 0.3 * len(labels)), labels


def _gift_intent(profile: AdvertiserProfile) -> bool:
    blob = " ".join(
        [
            profile.raw_query,
            profile.product or "",
            " ".join(profile.keywords),
            " ".join(profile.product_attributes),
        ]
    )
    return bool(_GIFT_MARK.search(blob))


def _mentions(profile: AdvertiserProfile, tag: str) -> bool:
    blob = " ".join(
        [
            profile.product or "",
            profile.category or "",
            profile.subcategory or "",
            " ".join(profile.keywords),
            " ".join(profile.product_attributes),
        ]
    ).lower()
    return bool(tag) and tag.replace("_", " ") in blob


def _age_overlap(advertiser: str, persona: str) -> float:
    a = _age_span(advertiser)
    b = _age_span(persona)
    if not a or not b:
        return 0.5
    lo, hi = max(a[0], b[0]), min(a[1], b[1])
    if hi < lo:
        return 0.15
    return 0.85


def _age_span(text: str) -> tuple[int, int] | None:
    nums = [int(t) for t in tokenize(text) if t.isdigit() and len(t) <= 3]
    if len(nums) >= 2:
        return min(nums[0], nums[1]), max(nums[0], nums[1])
    if len(nums) == 1:
        n = nums[0]
        return n, n + 10
    return None


def _gender_token(text: str) -> str:
    """Female before male: 'male' is a substring of 'female', 'men' of 'women'."""
    t = text.lower()
    if "balanced" in t:
        return "balanced"
    if "female" in t or "women" in t or t == "woman":
        return "female"
    if "male" in t or "men" in t or t == "man":
        return "male"
    return ""


def _gender_fit(advertiser: str, skew: str) -> float:
    a, s = _gender_token(advertiser), _gender_token(skew)
    if "balanced" in (a, s):
        return 0.7
    if a and s and a == s:
        return 0.9
    if a and s and a != s:
        return 0.2
    return 0.55


def speak_as(persona_id: str, fallback: str = "") -> str:
    return PERSONA_SPEAK.get(persona_id) or fallback


def prefer_matches(picked: str, matches: list[PersonaMatch]) -> list[PersonaMatch]:
    needle = picked.strip().lower()
    if not needle:
        return matches
    preferred = [
        row
        for row in matches
        if needle in speak_as(row.persona_id).lower() or needle in row.persona_name.lower()
    ]
    if not preferred:
        return matches
    rest = [row for row in matches if row not in preferred]
    return preferred + rest


def persona_why(row: PersonaMatch) -> str:
    if "shopping for gifts" in row.match_signals:
        return "gifting is already in the brief"
    for signal in row.match_signals:
        if signal.startswith("fits "):
            return "they already shop " + signal.removeprefix("fits ")
    return "a plausible shopper for this product"


def render_personas(matches: list[PersonaMatch]) -> str:
    if not matches:
        return ""
    blocks: list[str] = []
    for row in matches[:4]:
        name = row.persona_name or speak_as(row.persona_id)
        who = speak_as(row.persona_id, row.persona_name)
        blocks.append(f"{name}\n{who}\n{persona_why(row)}")
    return "\n\n".join(blocks)
