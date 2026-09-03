"""Advertiser understanding: structured extract, no invented demographics."""

from __future__ import annotations

import logging
import re

from app.llm import llm_enabled, load_prompt, parse_structured
from app.ranking import INSUFFICIENT_PROFILE_CONFIDENCE
from app.schemas import AdvertiserProfile, AudienceHint, ExtractedProfile, PricePosition

log = logging.getLogger(__name__)

_VAGUE = re.compile(
    r"\b(idk|i don'?t know|just try( it)?|feel better|new kind of thing|help people)\b",
    re.I,
)
# Require $ or thousands-commas so years/zips like 2020 are not prices. The $ sigil
# is enough on its own, so two digits are allowed there ("$40") but not bare.
_PRICE = re.compile(r"\$\s*([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{2,5})|(?<![0-9])([0-9]{1,3}(?:,[0-9]{3})+)(?![0-9])")
_SUBSCRIPTION = re.compile(r"\bsubscription(-based)?\b", re.I)

# Phrase → product facts supported by that phrase. Not a publisher map.
_PHRASES: list[tuple[re.Pattern[str], dict]] = [
    (
        re.compile(r"single\s*malts?", re.I),
        {
            "product": "single malt whisky",
            "category": "alcohol",
            "subcategory": "whisky",
            "attrs": ["single_malt", "assortment"],
            "keywords": ["single malt", "whisky", "spirits", "alcohol"],
        },
    ),
    (
        re.compile(r"\bwhisk(?:e)?y\b", re.I),
        {
            "product": "whisky",
            "category": "alcohol",
            "subcategory": "whisky",
            "attrs": [],
            "keywords": ["whisky", "spirits", "alcohol"],
        },
    ),
    (
        re.compile(r"dog food", re.I),
        {
            "product": "dog food",
            "category": "pet",
            "subcategory": "pet_food",
            "attrs": [],
            "keywords": ["dog food", "dog", "pet", "pet food"],
        },
    ),
    (
        re.compile(r"activewear", re.I),
        {
            "product": "activewear",
            "category": "apparel",
            "subcategory": "activewear",
            "attrs": [],
            "keywords": ["activewear", "apparel", "fitness"],
        },
    ),
    (
        re.compile(r"non-?alcoholic|functional (drink|beverage)|adaptogen", re.I),
        {
            "product": "non-alcoholic functional drink",
            "category": "beverages",
            "subcategory": "functional_beverages",
            "attrs": ["non_alcoholic", "functional"],
            "keywords": ["non-alcoholic", "functional beverage", "drink", "beverages"],
        },
    ),
    (
        re.compile(r"candles?", re.I),
        {
            "product": "candles",
            "category": "home",
            "subcategory": "home_fragrance",
            "attrs": ["small_batch"],
            "keywords": ["candles", "home", "gifting"],
        },
    ),
    (
        re.compile(r"outerwear|\bski\b", re.I),
        {
            "product": "backcountry ski outerwear",
            "category": "apparel",
            "subcategory": "outerwear",
            "attrs": ["technical", "backcountry"],
            "keywords": ["outerwear", "ski", "apparel", "technical"],
        },
    ),
    (
        re.compile(r"\b(saas|dental practices?)\b", re.I),
        {
            "product": "B2B SaaS for dental practices",
            "category": "software",
            "subcategory": "b2b_saas",
            "attrs": ["b2b"],
            "keywords": ["saas", "dental", "b2b", "software"],
        },
    ),
    (
        re.compile(r"cleaning products?", re.I),
        {
            "product": "refillable cleaning products",
            "category": "household",
            "subcategory": "cleaning",
            "attrs": ["refillable"],
            "keywords": ["cleaning", "household", "refillable"],
        },
    ),
    (
        re.compile(r"handbags?", re.I),
        {
            "product": "custom leather handbags",
            "category": "accessories",
            "subcategory": "handbags",
            "attrs": ["leather", "custom"],
            "keywords": ["handbags", "leather", "accessories"],
        },
    ),
    (
        re.compile(r"protein bars?", re.I),
        {
            "product": "protein bars",
            "category": "food",
            "subcategory": "nutrition_bars",
            "attrs": [],
            "keywords": ["protein bars", "protein", "snacks", "pantry", "grocery"],
        },
    ),
    (
        re.compile(r"cat[- ]owners?|cat'?s life", re.I),
        {
            "product": "cat-owner subscription box",
            "category": "pet",
            "subcategory": "pet_supplies",
            "attrs": ["subscription_box"],
            "keywords": ["cat", "pet", "subscription box", "pet supplies"],
        },
    ),
    (
        re.compile(r"supplements?|pre-workout|creatine", re.I),
        {
            "product": "workout supplements",
            "category": "wellness",
            "subcategory": "supplements",
            "attrs": [],
            "keywords": ["supplements", "workout", "protein", "wellness"],
        },
    ),
    (
        re.compile(r"\bbedding\b|\blinen\b", re.I),
        {
            "product": "linen bedding",
            "category": "home",
            "subcategory": "bedding",
            "attrs": ["linen"],
            "keywords": ["bedding", "linen", "home textiles"],
        },
    ),
]


def _price_position(text: str) -> PricePosition:
    amount = _PRICE.search(text)
    if amount:
        n = float((amount.group(1) or amount.group(2)).replace(",", ""))
        if n >= 600:
            return "luxury"
        if n >= 150:
            return "premium"
        if n >= 50:
            return "mid"
        return "budget"
    low = text.lower()
    if "luxury" in low or "best quality money" in low:
        return "luxury"
    if "premium" in low or "lululemon" in low:
        return "premium"
    if re.search(r"\b(budget|half the cost|compete on price)\b", low):
        return "budget"
    return "unknown"


def _audience(text: str) -> AudienceHint | None:
    low = text.lower()
    gender = "women" if re.search(r"\b(women'?s|for women)\b", low) else None
    if re.search(r"\bfor men\b", low):
        gender = "men"
    interests: list[str] = []
    if "joint health" in low:
        interests.append("joint health")
    if "moms" in low or "mothers" in low:
        interests.append("parents")
    if not gender and not interests:
        return None
    return AudienceHint(gender=gender, interests=interests)


def _attrs_from_text(text: str, base: list[str]) -> list[str]:
    low = text.lower()
    extra = list(base)
    for token, tag in (
        ("senior", "senior"),
        ("grain-free", "grain_free"),
        ("vet-formulated", "vet_formulated"),
        ("sustainable", "sustainable"),
        ("recycled", "recycled"),
        ("refillable", "refillable"),
        ("small-batch", "small_batch"),
        ("technical", "technical"),
    ):
        if token in low and tag not in extra:
            extra.append(tag)
    return extra


def extract_heuristic(text: str) -> AdvertiserProfile:
    """Conservative no-LLM path. Does not invent missing audience/price/geo."""
    hit: dict | None = None
    for pattern, spec in _PHRASES:
        if pattern.search(text):
            hit = spec
            break
    if hit is None:
        ambiguities = ["What product/service is being sold?"]
        if _VAGUE.search(text) or len(text.split()) < 6:
            ambiguities.append("Who is the target customer?")
        return AdvertiserProfile(
            raw_query=text,
            confidence=0.18 if _VAGUE.search(text) else 0.28,
            ambiguities=ambiguities,
        )

    product = hit["product"]
    if re.search(r"women'?s|for women", text, re.I) and "activewear" in product:
        product = "women's activewear"
    keywords = list(hit["keywords"])
    if "sustainable" in text.lower() and "sustainable" not in keywords:
        keywords.append("sustainable")
    confidence = 0.86
    ambiguities: list[str] = []
    if hit["category"] == "software" or hit.get("subcategory") == "home_fragrance":
        ambiguities.append("No catalog shelf is guaranteed for this product.")
        confidence = 0.8
    return AdvertiserProfile(
        raw_query=text,
        category=hit["category"],
        subcategory=hit["subcategory"],
        product=product,
        product_attributes=_attrs_from_text(text, list(hit["attrs"])),
        keywords=keywords,
        audience=_audience(text),
        price_position=_price_position(text),
        business_model="subscription" if _SUBSCRIPTION.search(text) else None,
        geography=[],
        confidence=confidence,
        ambiguities=ambiguities,
    )


def extract_profile(text: str) -> AdvertiserProfile:
    query = text.strip()
    if not query:
        return AdvertiserProfile(
            raw_query="",
            confidence=0.05,
            ambiguities=["What product/service is being sold?"],
        )
    if llm_enabled():
        try:
            extracted = parse_structured(
                load_prompt("advertiser_understanding.md"),
                query,
                ExtractedProfile,
            )
            profile = AdvertiserProfile(raw_query=query, **extracted.model_dump())
            return validate_profile(profile)
        except Exception:
            log.warning("LLM extract failed; falling back to heuristic", exc_info=True)
    return validate_profile(extract_heuristic(query))


def validate_profile(profile: AdvertiserProfile) -> AdvertiserProfile:
    data = profile.model_copy(deep=True)
    if data.category == "":
        data.category = None
    if data.subcategory == "":
        data.subcategory = None
    if data.product == "":
        data.product = None
    if data.business_model == "":
        data.business_model = None
    if _VAGUE.search(data.raw_query) and not data.product:
        data.confidence = min(data.confidence, 0.22)
        if "What product/service is being sold?" not in data.ambiguities:
            data.ambiguities.append("What product/service is being sold?")
        if "Who is the target customer?" not in data.ambiguities:
            data.ambiguities.append("Who is the target customer?")
    if not data.product and not data.category:
        data.confidence = min(data.confidence, INSUFFICIENT_PROFILE_CONFIDENCE - 0.05)
    data.confidence = max(0.0, min(1.0, data.confidence))
    return data


def clarification_question(profile: AdvertiserProfile) -> str:
    if profile.ambiguities:
        first = profile.ambiguities[0]
        if first.endswith("?"):
            return first
    return "What product or product family are you advertising?"
