"""Deterministic campaign config. LLM writes the explanation only."""

from __future__ import annotations

import logging
import math
import re
from calendar import monthrange
from datetime import date
from typing import Iterable

from app.llm import llm_enabled, load_prompt, parse_structured
from app.ranking import audience_fit, economic_fit
from app.schemas import (
    AdvertiserProfile,
    BidRange,
    BidStrategy,
    CampaignConfig,
    CampaignExplanation,
    CampaignInputs,
    CampaignObjective,
    CampaignTargeting,
    MissingQuestion,
    PersonaMatch,
    Publisher,
    PublisherAllocation,
    PublisherContext,
    ShopperPersona,
)

log = logging.getLogger(__name__)

# Heuristic starting bids — not catalog or market observations.
BID_HEURISTICS: dict[CampaignObjective, BidStrategy] = {
    "awareness": BidStrategy(type="CPM", starting_bid_range=BidRange(min=6.0, max=14.0)),
    "traffic": BidStrategy(type="CPC", starting_bid_range=BidRange(min=0.8, max=2.0)),
    "conversions": BidStrategy(type="CPA", starting_bid_range=BidRange(min=12.0, max=40.0)),
}

ALLOC_MIN_PCT = 0.08
ALLOC_MAX_PCT = 0.55
OBJECTIVE_LABEL = {
    "awareness": "Build awareness",
    "traffic": "Drive traffic",
    "conversions": "Drive purchases",
}

_BUDGET = re.compile(
    r"(?:\$\s*([0-9]{1,3}(?:,[0-9]{3})+(?:\.\d+)?|[0-9]+(?:\.\d+)?)\s*k\b)"
    r"|(?:\$\s*([0-9]{1,3}(?:,[0-9]{3})+(?:\.\d+)?))"
    r"|(?:(?<![0-9])([0-9]+(?:\.\d+)?)\s*k\b)",
    re.I,
)
_DAYS = re.compile(r"\b(\d{1,3})\s*days?\b", re.I)
_WEEKS = re.compile(r"\b(\d+|one|two|three|four)\s*weeks?\b", re.I)
_MONTHS = re.compile(r"\b(\d+|one|a|about a|approximately a)\s*months?\b", re.I)
_UNTIL_MONTH = re.compile(
    r"until(?:\s+the)?\s+end\s+of\s+(january|february|march|april|may|june|"
    r"july|august|september|october|november|december)",
    re.I,
)
_WORDS = {"one": 1, "a": 1, "about a": 1, "approximately a": 1, "two": 2, "three": 3, "four": 4}
_MONTH_INDEX = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_OBJECTIVE = (
    (re.compile(r"\b(?:to\s+)?(?:drive|driving)\s+purchases?\b", re.I), "conversions"),
    (re.compile(r"\b(?:conversions?|roas|cpa)\b", re.I), "conversions"),
    (re.compile(r"\b(?:to\s+)?(?:drive|driving)\s+(?:traffic|visits?|clicks?)\b", re.I), "traffic"),
    (re.compile(r"\b(?:to\s+)?(?:build|building)\s+awareness\b", re.I), "awareness"),
    (re.compile(r"\bin front of (?:more )?people\b", re.I), "awareness"),
)
_REVISION_GLUE = re.compile(
    r"\b(actually|instead|please|just|let'?s|make it|change(?: it)?(?: to)?|"
    r"update(?: it)?(?: to)?|set(?: it)?(?: to)?|budget|spend|total|campaign|"
    r"it|to|the|a|an|for|about|approximately|around|over|lasting|"
    r"run(?:ning)?|until|end|of)\b",
    re.I,
)
_AGE = re.compile(r"(\d{2})")


def extract_campaign_inputs(text: str, *, today: date | None = None) -> CampaignInputs:
    blob = (text or "").strip()
    if not blob:
        return CampaignInputs()
    return CampaignInputs(
        objective=_parse_objective(blob),
        # A budget-only edit ("$500", "make it 500") gets the loose answer parser;
        # prose keeps the strict one so "$40 candles" is a price, not a budget.
        total_budget_usd=parse_budget_answer(blob) if _is_budget_only(blob) else _parse_budget(blob),
        duration_days=_parse_duration(blob, today=today),
        performance_goal=_parse_performance_goal(blob),
    )


def is_campaign_revision(text: str) -> bool:
    """True for campaign edits (chips, budget-only, duration-only), not product copy."""
    blob = (text or "").strip()
    if not blob:
        return False
    low = blob.lower().strip(".!")
    if low in {label.lower() for label in OBJECTIVE_LABEL.values()}:
        return True
    return _is_budget_only(blob) or _is_duration_only(blob)


def apply_campaign_answers(inputs: CampaignInputs, answers: list[dict] | None) -> CampaignInputs:
    data = inputs.model_copy(deep=True)
    for ans in answers or []:
        field = ans.get("field") or ""
        value = str(ans.get("value") or "").strip()
        if not value:
            continue
        if field == "campaign_objective":
            data.objective = _parse_objective(value) or data.objective
        elif field == "total_budget_usd":
            data.total_budget_usd = parse_budget_answer(value) or data.total_budget_usd
        elif field == "campaign_duration":
            data.duration_days = _parse_duration(value) or data.duration_days
        elif field == "performance_goal":
            data.performance_goal = value
    return data


def analyze_campaign_missing(
    inputs: CampaignInputs,
    *,
    skipped_fields: list[str] | None = None,
    asked_fields: list[str] | None = None,
) -> MissingQuestion | None:
    skipped = set(skipped_fields or [])
    asked = set(asked_fields or [])
    if inputs.objective is None:
        return MissingQuestion(
            field="campaign_objective",
            importance="required",
            question="What matters most here — getting the brand in front of more people, driving visits, or driving purchases?",
            quick_replies=["Build awareness", "Drive traffic", "Drive purchases"],
            allow_free_text=True,
            allow_skip=False,
        )
    if inputs.total_budget_usd is None:
        chips = budget_chip_labels()
        return MissingQuestion(
            field="total_budget_usd",
            importance="required",
            question="What's the total budget? Pick a starting point or type an amount.",
            quick_replies=chips,
            allow_free_text=True,
            allow_skip=False,
        )
    if inputs.duration_days is None:
        budget = (
            f"Got it — I'll work with a ${_pretty_money(inputs.total_budget_usd)} budget. "
            if inputs.total_budget_usd
            else ""
        )
        return MissingQuestion(
            field="campaign_duration",
            importance="required",
            question=f"{budget}How long are you thinking of running the campaign?",
            quick_replies=["2 weeks", "About a month", "3 months"],
            allow_free_text=True,
            allow_skip=False,
        )
    if (
        inputs.objective == "conversions"
        and not inputs.performance_goal
        and "performance_goal" not in skipped
        and "performance_goal" not in asked
    ):
        return MissingQuestion(
            field="performance_goal",
            importance="useful",
            question="Any CPA or ROAS target I should plan around, or should we start without one?",
            quick_replies=["$25 CPA", "3x ROAS", "Just start converting"],
            allow_free_text=True,
            allow_skip=True,
        )
    return None


def build_targeting(
    profile: AdvertiserProfile,
    matches: list[PersonaMatch],
    contexts: list[PublisherContext],
    personas: Iterable[ShopperPersona],
) -> CampaignTargeting:
    by_id = {row.id: row for row in personas}
    picked = [by_id[m.persona_id] for m in matches if m.persona_id in by_id]
    aud = profile.audience
    targeting = CampaignTargeting()

    stated_age = aud.age_range if aud and aud.age_range else None
    if not stated_age:
        hit = re.search(r"\b(?:aged?|ages)\s+(\d{2})\s*[-–to]+\s*(\d{2})\b", profile.raw_query, re.I)
        if hit:
            stated_age = f"{hit.group(1)}-{hit.group(2)}"
    if stated_age:
        targeting.age_range = stated_age
        targeting.age_range_source = "advertiser"
    else:
        bounds = [_age_bounds(p.age_range) for p in picked]
        pub_bounds = [_age_bounds(ctx.audience.age_skew) for ctx in contexts]
        known = [b for b in (*bounds, *pub_bounds) if b]
        if known:
            lo, hi = min(b[0] for b in known), max(b[1] for b in known)
            targeting.age_range = f"{lo}–{hi}"
            targeting.age_range_source = "persona" if any(bounds) else "publisher"

    if aud and aud.gender:
        targeting.gender = aud.gender
        targeting.gender_source = "advertiser"
    else:
        gender = _shared_gender(picked, contexts)
        if gender:
            targeting.gender = gender
            targeting.gender_source = "persona"

    if profile.geography:
        targeting.geographies = list(profile.geography)
        targeting.geography_source = "advertiser"

    interests: list[str] = []
    if aud:
        interests.extend(aud.interests)
    for persona in picked:
        for item in persona.category_affinities:
            label = item.replace("_", " ")
            if label not in interests:
                interests.append(label)
            if len(interests) >= 6:
                break
    targeting.interests = interests[:6]

    signals: list[str] = []
    for persona in picked:
        for item in persona.messaging_preferences[:1]:
            if item not in signals:
                signals.append(item)
    targeting.behavioral_signals = signals[:4]
    return targeting


def allocate_publishers(
    chosen: list[dict],
    contexts: list[PublisherContext],
    profile: AdvertiserProfile,
    total_usd: float,
) -> tuple[list[PublisherAllocation], list[str]]:
    warnings: list[str] = []
    ctx_by_id = {ctx.publisher_id: ctx for ctx in contexts}
    eligible = [
        row
        for row in chosen
        if row.get("match_strength") in {"strong", "moderate"} and row.get("publisher_id") in ctx_by_id
    ]
    if not eligible:
        warnings.append(
            "No publisher met a strong or moderate match threshold, so I am not splitting budget across the catalog."
        )
        return [], warnings

    scores: list[float] = []
    rows: list[dict] = []
    impressions = [max(1, ctx_by_id[row["publisher_id"]].monthly_impressions) for row in eligible]
    max_reach = math.log1p(max(impressions))
    for row, raw_imp in zip(eligible, impressions):
        ctx = ctx_by_id[row["publisher_id"]]
        publisher = _publisher_from_context(ctx)
        fit, _ = audience_fit(profile, publisher)
        reach = math.log1p(raw_imp) / max_reach if max_reach else 1.0
        quality = max(0.05, float(row.get("score") or 0.0))
        scores.append(quality * max(0.15, fit) * (0.55 + 0.45 * reach))
        rows.append(row)
        econ = economic_fit(profile, publisher)
        if profile.price_position != "unknown" and econ < 0.4:
            warnings.append(
                f"{ctx.publisher_name}'s typical order value sits apart from this product's price band — "
                "treat that as a conversion-quality watch-out, not a reason to spend more."
            )

    weights = _guardrail_weights(scores)
    pcts = _percentages(weights)
    dollars = _reconcile_dollars(pcts, total_usd)
    allocations = [
        PublisherAllocation(
            publisher_id=row["publisher_id"],
            publisher_name=row["publisher_name"],
            allocation_pct=pct,
            allocation_usd=usd,
            match_score=float(row.get("score") or 0.0),
            confidence=float(row.get("confidence") or 0.0),
        )
        for row, pct, usd in zip(rows, pcts, dollars)
    ]
    return allocations, warnings


def recommend_bid(inputs: CampaignInputs) -> BidStrategy:
    assert inputs.objective is not None
    bid = BID_HEURISTICS[inputs.objective].model_copy(deep=True)
    return bid


def budget_chip_labels() -> list[str]:
    """Demo starter amounts. Type-in answers use parse_budget_answer."""
    return ["$100", "$500", "$2,000"]


def build_campaign_config(
    profile: AdvertiserProfile,
    inputs: CampaignInputs,
    chosen: list[dict],
    contexts: list[PublisherContext],
    matches: list[PersonaMatch],
    personas: Iterable[ShopperPersona],
    *,
    skipped_fields: list[str] | None = None,
) -> CampaignConfig:
    if not inputs.has_required():
        raise ValueError("campaign inputs are incomplete")
    assert inputs.objective is not None
    assert inputs.total_budget_usd is not None
    assert inputs.duration_days is not None
    daily = inputs.total_budget_usd / inputs.duration_days
    targeting = build_targeting(profile, matches, contexts, personas)
    publishers, warnings = allocate_publishers(chosen, contexts, profile, inputs.total_budget_usd)
    skipped = set(skipped_fields or [])
    confidence = 0.78
    if not publishers:
        confidence = 0.35
    if "performance_goal" in skipped:
        confidence -= 0.08
        warnings.append("No CPA/ROAS target was given, so the bid range is a generic starting point.")
    if targeting.age_range_source in {"persona", "publisher"}:
        warnings.append("Age targeting is inferred from audience signals, not an advertiser-stated range.")
    bid = recommend_bid(inputs)
    config = CampaignConfig(
        objective=inputs.objective,
        total_budget_usd=inputs.total_budget_usd,
        duration_days=inputs.duration_days,
        daily_budget_usd=daily,
        targeting=targeting,
        publishers=publishers,
        bid_strategy=bid,
        confidence=max(0.2, min(1.0, confidence)),
        warnings=warnings,
    )
    return validate_campaign_config(config, inputs, chosen)


def validate_campaign_config(
    config: CampaignConfig,
    inputs: CampaignInputs,
    chosen: list[dict],
) -> CampaignConfig:
    data = config.model_copy(deep=True)
    if data.total_budget_usd <= 0 or data.duration_days <= 0:
        raise ValueError("budget and duration must be positive")
    data.daily_budget_usd = data.total_budget_usd / data.duration_days
    known = {row["publisher_id"] for row in chosen}
    data.publishers = [row for row in data.publishers if row.publisher_id in known]
    if data.publishers:
        pcts = [row.allocation_pct for row in data.publishers]
        if abs(sum(pcts) - 100.0) > 0.01 or any(p < 0 for p in pcts):
            weights = [max(0.0, p) for p in pcts] or [1.0] * len(pcts)
            fixed = _percentages(_guardrail_weights(weights))
            dollars = _reconcile_dollars(fixed, data.total_budget_usd)
            for row, pct, usd in zip(data.publishers, fixed, dollars):
                row.allocation_pct = pct
                row.allocation_usd = usd
        else:
            dollars = _reconcile_dollars([row.allocation_pct for row in data.publishers], data.total_budget_usd)
            for row, usd in zip(data.publishers, dollars):
                row.allocation_usd = usd
    expected = BID_HEURISTICS[data.objective]
    if data.bid_strategy.type != expected.type:
        data.bid_strategy = expected.model_copy(deep=True)
    data.bid_strategy.basis = "heuristic"
    return data


def render_campaign_text(config: CampaignConfig, explanation: str) -> str:
    lines = [
        "**Campaign objective**",
        OBJECTIVE_LABEL[config.objective],
        "**Budget**",
        f"${_pretty_money(config.total_budget_usd)} over {config.duration_days} days",
        f"~${_pretty_money(config.daily_budget_usd)}/day",
    ]
    lines.append("**Publisher allocation**")
    if config.publishers:
        width = max(len(row.publisher_name) for row in config.publishers)
        for row in config.publishers:
            lines.append(
                f"{row.publisher_name.ljust(width)}  {row.allocation_pct:.0f}%   ${_pretty_money(row.allocation_usd)}"
            )
    else:
        lines.append("No strong publisher match — not splitting budget yet.")
    lines.append("**Targeting**")
    targeting = _targeting_lines(config.targeting)
    lines.extend(targeting or ["Audience signals we actually have — no invented demographics."])
    lines.append("**Bid strategy**")
    bid = config.bid_strategy
    lines.append(
        f"{bid.type}-oriented, heuristic starting range ${bid.starting_bid_range.min:g}–${bid.starting_bid_range.max:g}"
    )
    if explanation:
        lines.append("**Why this setup**")
        lines.append(explanation.strip())
    if config.warnings:
        lines.append("**Assumptions**")
        for note in config.warnings:
            lines.append(f"• {note}")
    return "\n".join(lines)


def explain_campaign(
    config: CampaignConfig,
    profile: AdvertiserProfile,
    matches: list[PersonaMatch],
) -> str:
    if not llm_enabled():
        return _explain_heuristic(config, profile, matches)
    payload = {
        "config": config.model_dump(),
        "product": profile.product,
        "personas": [row.persona_name for row in matches],
        "warnings": config.warnings,
    }
    try:
        parsed = parse_structured(
            load_prompt("campaign_strategist.md"),
            str(payload),
            CampaignExplanation,
        )
        return parsed.explanation.strip()
    except Exception:
        log.warning("LLM campaign strategist failed; falling back to heuristic", exc_info=True)
        return _explain_heuristic(config, profile, matches)


def finalize_campaign(
    profile: AdvertiserProfile,
    inputs: CampaignInputs,
    chosen: list[dict],
    contexts: list[PublisherContext],
    matches: list[PersonaMatch],
    personas: Iterable[ShopperPersona],
    *,
    skipped_fields: list[str] | None = None,
) -> tuple[CampaignConfig, str]:
    config = build_campaign_config(
        profile, inputs, chosen, contexts, matches, personas, skipped_fields=skipped_fields
    )
    explanation = explain_campaign(config, profile, matches)
    return config, render_campaign_text(config, explanation)


def _parse_budget(text: str) -> float | None:
    blob = text.lower()
    match = _BUDGET.search(text)
    if not match:
        return None
    k_money, comma_money, bare_k = match.groups()
    if k_money:
        value = float(k_money.replace(",", "")) * 1000
    elif comma_money:
        value = float(comma_money.replace(",", ""))
    else:
        value = float(bare_k) * 1000
    if value < 1000 and not re.search(r"\b(budget|spend|campaign)\b", blob):
        return None
    return value


_ANSWER_AMOUNT = re.compile(
    r"(?:\$\s*)?([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.\d+)?\s*(k\b|dollars?)?",
    re.I,
)


def parse_budget_answer(text: str) -> float | None:
    """Loose parse for a dedicated budget answer: 200, $200, 200 dollars, 2k."""
    blob = (text or "").strip()
    if not blob:
        return None
    match = _BUDGET.search(blob)
    if match:
        k_money, comma_money, bare_k = match.groups()
        if k_money:
            value = float(k_money.replace(",", "")) * 1000
        elif comma_money:
            value = float(comma_money.replace(",", ""))
        else:
            value = float(bare_k) * 1000
        return value if value >= 1 else None
    loose = _ANSWER_AMOUNT.search(blob)
    if not loose:
        return None
    value = float(loose.group(1).replace(",", ""))
    unit = (loose.group(2) or "").lower()
    if unit.startswith("k"):
        value *= 1000
    return value if value >= 1 else None


def _parse_duration(text: str, *, today: date | None = None) -> int | None:
    until = _UNTIL_MONTH.search(text)
    if until:
        month = _MONTH_INDEX[until.group(1).lower()]
        ref = today or date.today()
        year = ref.year if (month > ref.month or (month == ref.month and ref.day < 28)) else ref.year + 1
        end = date(year, month, monthrange(year, month)[1])
        days = (end - ref).days
        return days if days > 0 else None
    days = _DAYS.search(text)
    if days:
        n = int(days.group(1))
        return n if n > 0 else None
    weeks = _WEEKS.search(text)
    if weeks:
        n = _int_word(weeks.group(1)) * 7
        return n if n > 0 else None
    months = _MONTHS.search(text)
    if months:
        n = _int_word(months.group(1)) * 30
        return n if n > 0 else None
    return None


def _parse_objective(text: str) -> CampaignObjective | None:
    low = text.lower().strip()
    for label, value in OBJECTIVE_LABEL.items():
        if low == label.lower() or low == value.lower():
            return label
    for pattern, value in _OBJECTIVE:
        if pattern.search(text):
            return value
    return None


def _stripped_campaign_glue(text: str) -> str:
    leftover = _REVISION_GLUE.sub(" ", text)
    return re.sub(r"[^\w]+", " ", leftover).strip()


def _is_duration_only(text: str) -> bool:
    if _parse_duration(text) is None:
        return False
    leftover = _UNTIL_MONTH.sub(" ", text)
    leftover = _DAYS.sub(" ", leftover)
    leftover = _WEEKS.sub(" ", leftover)
    leftover = _MONTHS.sub(" ", leftover)
    return _stripped_campaign_glue(leftover) == ""


def _is_budget_only(text: str) -> bool:
    if parse_budget_answer(text) is None:
        return False
    leftover = _BUDGET.sub(" ", text)
    leftover = _ANSWER_AMOUNT.sub(" ", leftover)
    return _stripped_campaign_glue(leftover) == ""


def _parse_performance_goal(text: str) -> str | None:
    match = re.search(r"\b(\$?\d+(?:\.\d+)?\s*cpa|\d+(?:\.\d+)?x\s*roas)\b", text, re.I)
    return match.group(1) if match else None


def _int_word(raw: str) -> int:
    key = raw.lower().strip()
    return _WORDS.get(key, int(key) if key.isdigit() else 1)


def _age_bounds(text: str) -> tuple[int, int] | None:
    nums = [int(n) for n in _AGE.findall(text or "")]
    if not nums:
        return None
    return min(nums), max(nums)


def _shared_gender(personas: list[ShopperPersona], contexts: list[PublisherContext]) -> str | None:
    flags: list[str] = []
    for persona in personas:
        skew = (persona.gender_skew or "").lower()
        if "balanced" in skew:
            return None
        if "female" in skew:
            flags.append("female")
        elif "male" in skew:
            flags.append("male")
    for ctx in contexts:
        split = ctx.audience.gender_split
        female = split.get("female", 0.5)
        male = split.get("male", 0.5)
        if abs(female - male) < 0.2:
            return None
        flags.append("female" if female > male else "male")
    if flags and all(item == flags[0] for item in flags):
        return "women" if flags[0] == "female" else "men"
    return None


def _publisher_from_context(ctx: PublisherContext) -> Publisher:
    return Publisher(
        id=ctx.publisher_id,
        name=ctx.publisher_name,
        category=ctx.category,
        subcategories=ctx.subcategories,
        monthly_impressions=ctx.monthly_impressions,
        avg_order_value_usd=ctx.avg_order_value_usd,
        audience=ctx.audience,
        notes=ctx.notes,
    )


def _guardrail_weights(scores: list[float]) -> list[float]:
    n = len(scores)
    if n == 0:
        return []
    if n == 1:
        return [1.0]
    lo, hi = ALLOC_MIN_PCT, ALLOC_MAX_PCT
    leftover = 1.0 - lo * n
    if leftover < 0:
        return [1.0 / n] * n
    raw = [max(0.0, s) for s in scores]
    if not any(raw):
        raw = [1.0] * n
    extra = [0.0] * n
    free = set(range(n))
    cap = hi - lo
    while free:
        mass = sum(raw[i] for i in free) or float(len(free))
        trial = {i: leftover * raw[i] / mass for i in free}
        capped = [i for i in free if trial[i] > cap]
        if not capped:
            for i in free:
                extra[i] = trial[i]
            break
        for i in capped:
            extra[i] = cap
            leftover -= cap
            free.remove(i)
    return [lo + extra[i] for i in range(n)]


def _percentages(weights: list[float]) -> list[float]:
    if not weights:
        return []
    exact = [w * 100.0 for w in weights]
    floored = [int(x) for x in exact]
    leftover = 100 - sum(floored)
    order = sorted(range(len(exact)), key=lambda i: exact[i] - floored[i], reverse=True)
    for i in order[: max(leftover, 0)]:
        floored[i] += 1
    return [float(p) for p in floored]


def _reconcile_dollars(pcts: list[float], total: float) -> list[float]:
    raw = [round(total * p / 100.0, 2) for p in pcts]
    drift = round(total - sum(raw), 2)
    if drift and raw:
        i = max(range(len(raw)), key=lambda j: pcts[j])
        raw[i] = round(raw[i] + drift, 2)
    return raw


def _pretty_money(value: float) -> str:
    if abs(value - round(value)) < 0.005:
        return f"{int(round(value)):,}"
    return f"{value:,.2f}"


def _targeting_lines(targeting: CampaignTargeting) -> list[str]:
    lines: list[str] = []
    if targeting.age_range:
        extra = " (from audience signals)" if targeting.age_range_source != "advertiser" else ""
        lines.append(f"Ages {targeting.age_range}{extra}")
    if targeting.gender:
        extra = " (from audience signals)" if targeting.gender_source != "advertiser" else ""
        lines.append(f"{targeting.gender.capitalize()}{extra}")
    if targeting.geographies:
        lines.append(", ".join(targeting.geographies))
    if targeting.interests:
        lines.append(", ".join(targeting.interests[:4]))
    if targeting.behavioral_signals:
        lines.append(", ".join(targeting.behavioral_signals[:3]))
    return lines


def _explain_heuristic(
    config: CampaignConfig,
    profile: AdvertiserProfile,
    matches: list[PersonaMatch],
) -> str:
    product = profile.product or "this product"
    objective = OBJECTIVE_LABEL[config.objective].lower()
    if not config.publishers:
        return (
            f"I'd hold the {_pretty_money(config.total_budget_usd)} / {config.duration_days}-day "
            f"{objective} plan until we have a stronger publisher fit for {product}."
        )
    lead = config.publishers[0]
    names = ", ".join(row.publisher_name for row in config.publishers[1:])
    shoppers = ", ".join(row.persona_name.removeprefix("The ") for row in matches[:2])
    more = f" {names} get the rest." if names else ""
    shopper = f" Copy leans toward {shoppers}." if shoppers else ""
    return (
        f"I'd start with a {config.duration_days}-day, ${_pretty_money(config.total_budget_usd)} "
        f"{objective} campaign for {product}. {lead.publisher_name} gets the largest share "
        f"({lead.allocation_pct:.0f}%) on match and audience fit.{more}{shopper} "
        f"The {config.bid_strategy.type} range is a heuristic starting point, not a market quote."
    )
