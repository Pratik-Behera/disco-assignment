"""Generate and validate ad variants. Claims stay in code, not the model."""

from __future__ import annotations

import json
import logging
import re

from app.llm import llm_enabled, load_prompt, parse_structured
from app.schemas import (
    AdvertiserProfile,
    CreativeBatch,
    CreativeVariant,
    PersonaMatch,
    PublisherContext,
    ShopperPersona,
)

log = logging.getLogger(__name__)

HEADLINE_MAX = 60
BODY_MAX = 160
CTA_MAX = 24

_CLAIM = re.compile(
    r"\b(\d+\s*%|%\s*off|discount|save\s*\$|guaranteed?|clinically|"
    r"#1|number one|studies show|certified|free shipping)\b",
    re.I,
)
_UNRELATED = (
    "dog food",
    "cat food",
    "ski",
    "outerwear",
    "vitamin",
    "supplement",
    "diaper",
    "candle",
    "saas",
)


def generate_creatives(
    profile: AdvertiserProfile,
    contexts: list[PublisherContext],
    matches: list[PersonaMatch],
    personas: list[ShopperPersona],
) -> list[CreativeVariant]:
    if not contexts or not matches or not profile.product:
        return []
    by_id = {p.id: p for p in personas}
    combos = _combos(contexts, matches, by_id)
    # The graph's validate_creatives node is the single gate; do not validate twice.
    return _llm_variants(profile, combos) if llm_enabled() else _heuristic_variants(profile, combos)


def validate_creatives(
    variants: list[CreativeVariant],
    profile: AdvertiserProfile,
) -> list[CreativeVariant]:
    kept: list[CreativeVariant] = []
    seen: list[str] = []
    for item in variants:
        if not _ok(item, profile, seen):
            continue
        kept.append(item)
        seen.append(_norm(item.headline))
        if len(kept) >= 5:
            break
    return kept


def _combos(
    contexts: list[PublisherContext],
    matches: list[PersonaMatch],
    personas: dict[str, ShopperPersona],
) -> list[tuple[PublisherContext, ShopperPersona]]:
    out: list[tuple[PublisherContext, ShopperPersona]] = []
    for ctx in contexts[:3]:
        for match in matches[:3]:
            persona = personas.get(match.persona_id)
            if persona:
                out.append((ctx, persona))
            if len(out) >= 4:
                return out
    return out


def _llm_variants(
    profile: AdvertiserProfile,
    combos: list[tuple[PublisherContext, ShopperPersona]],
) -> list[CreativeVariant]:
    payload = {
        "advertiser": {
            "product": profile.product,
            "category": profile.category,
            "attributes": profile.product_attributes,
            "price_position": profile.price_position,
        },
        "combinations": [
            {
                "publisher": ctx.model_dump(),
                "persona": {
                    "id": persona.id,
                    "name": persona.name,
                    "description": persona.description,
                    "messaging_preferences": persona.messaging_preferences,
                    "disinterested_in": persona.disinterested_in,
                },
            }
            for ctx, persona in combos
        ],
    }
    try:
        batch = parse_structured(
            load_prompt("ad_creative.md"),
            json.dumps(payload, indent=2),
            CreativeBatch,
        )
        return batch.variants
    except Exception:
        log.warning("LLM creative failed; falling back to heuristic", exc_info=True)
        return _heuristic_variants(profile, combos)


def _heuristic_variants(
    profile: AdvertiserProfile,
    combos: list[tuple[PublisherContext, ShopperPersona]],
) -> list[CreativeVariant]:
    product = profile.product or "this"
    out: list[CreativeVariant] = []
    angles = (
        ("quality", "Built for people who notice", f"{product} with the details that last.", "Shop now"),
        ("emotional", "For the ones you care about", f"Bring {product} into the everyday ritual.", "Find yours"),
        ("discovery", "A new shelf to explore", f"See how {product} sits next to what they already love.", "Explore"),
        ("occasion", "When the moment calls for it", f"{product} that looks like you meant it.", "Gift this"),
        ("convenience", "Less friction, same standard", f"Get {product} without the extra steps.", "Shop now"),
    )
    if not combos:
        return []
    for i, (angle, headline, body, cta) in enumerate(angles):
        ctx, persona = combos[i % len(combos)]
        if "giftable" in persona.messaging_preferences or persona.id == "persona_010":
            if angle == "occasion":
                headline = "Ready to wrap"
                body = f"{product} that feels like a gift, not a leftover."
                cta = "Gift this"
        if "heritage" in persona.messaging_preferences or "craftsmanship" in persona.messaging_preferences:
            if angle == "quality":
                headline = "Quiet quality"
                body = f"{product} for shoppers who prefer lasting over loud."
        if ctx.avg_order_value_usd >= 80 and angle == "quality":
            body = f"{product} for a higher-consideration cart."
        out.append(
            CreativeVariant(
                angle=angle,
                headline=headline,
                body=body,
                cta=cta,
                publisher_id=ctx.publisher_id,
                persona_id=persona.id,
            )
        )
    return out


def _ok(item: CreativeVariant, profile: AdvertiserProfile, seen: list[str]) -> bool:
    if not (item.angle and item.headline.strip() and item.body.strip() and item.cta.strip()):
        return False
    if len(item.headline) > HEADLINE_MAX or len(item.body) > BODY_MAX or len(item.cta) > CTA_MAX:
        return False
    blob = f"{item.headline} {item.body} {item.cta}"
    if _CLAIM.search(blob):
        return False
    product = (profile.product or "").lower()
    category = (profile.category or "").lower()
    for word in _UNRELATED:
        if word in blob.lower() and word not in product and word not in category:
            if word not in " ".join(profile.keywords).lower():
                return False
    key = _norm(item.headline)
    if key in seen:
        return False
    if any(_too_close(key, other) for other in seen):
        return False
    return True


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _too_close(a: str, b: str) -> bool:
    if a == b:
        return True
    aw, bw = set(a.split()), set(b.split())
    if not aw or not bw:
        return True
    return len(aw & bw) / len(aw | bw) > 0.7
