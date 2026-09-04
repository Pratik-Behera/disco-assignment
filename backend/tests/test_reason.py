from app.reason import reason_heuristic, render_text
from app.schemas import (
    AdvertiserProfile,
    ExclusionStats,
    FeatureEvidence,
    FeatureScores,
    NearMissReason,
    Publisher,
    PublisherAudience,
    PublisherReason,
    ReasoningResult,
    Recommendation,
    ScoredPublisher,
)


def _publisher(pid: str, name: str, category: str) -> Publisher:
    return Publisher(
        id=pid,
        name=name,
        category=category,
        subcategories=[],
        monthly_impressions=1000,
        avg_order_value_usd=20,
        audience=PublisherAudience(),
    )


def _rec() -> Recommendation:
    return Recommendation(
        publisher_id="pub_swiftcart",
        publisher_name="Swiftcart",
        score=0.61,
        confidence=0.72,
        match_strength="moderate",
        evidence=FeatureEvidence(
            category_match="Related shelf signal on Swiftcart",
            product_match="No assortment or audience listed for single malt whisky.",
        ),
    )


def test_render_text_compact_copy_remainder_and_near_miss_format() -> None:
    profile = AdvertiserProfile(raw_query="single malts", product="single malt whisky", confidence=0.85)
    rec = _rec()
    reasoning = ReasoningResult(
        recommendations=[
            PublisherReason(
                publisher_id=rec.publisher_id,
                headline="Swiftcart — Solid adjacent fit",
                why="Related shelf signal on Swiftcart",
                caveat="No assortment or audience listed for single malt whisky.",
            )
        ],
        near_misses=[
            NearMissReason(
                publisher_id="pub_popsip",
                publisher_name="Pop & Sip",
                explanation="Pop & Sip — close on the drinks shelf, but not a stronger fit than the names above.",
            )
        ],
        remainder="I left the rest of the catalog out — 12 are a different category, 6 are only a weak or indirect match.",
    )
    text = render_text(profile, [rec], reasoning, "ok")
    lines = text.splitlines()
    assert "\n\n" not in text
    assert all(line.strip() for line in lines)
    assert "• Related shelf signal on Swiftcart" in text
    assert "• Caveat: No assortment or audience listed for single malt whisky." in text
    assert "• Pop & Sip — close on the drinks shelf, but not a stronger fit than the names above." in text
    assert lines[-1] == "I left the rest of the catalog out — 12 are a different category, 6 are only a weak or indirect match."
    assert text.lower().count("no assortment") == 1
    assert "**Swiftcart**" in text


def test_render_text_drops_no_advertiser_audience_caveat() -> None:
    profile = AdvertiserProfile(raw_query="dog food", product="dog food", confidence=0.8)
    rec = Recommendation(
        publisher_id="pub_pawline",
        publisher_name="Pawline",
        score=0.8,
        confidence=0.8,
        match_strength="strong",
        evidence=FeatureEvidence(category_match="Direct pet food overlap"),
    )
    reasoning = ReasoningResult(
        recommendations=[
            PublisherReason(
                publisher_id=rec.publisher_id,
                headline="Pawline — Strongest available fit",
                why="Direct pet food overlap",
                caveat="No advertiser audience was stated. Publisher audience is unused.",
            )
        ],
        remainder="I left the rest of the catalog out — 10 are a different category, 8 are only a weak or indirect match.",
    )
    text = render_text(profile, [rec], reasoning, "ok")
    assert "No advertiser audience" not in text
    assert "• Caveat:" not in text
    assert "• Direct pet food overlap" in text


def test_reason_heuristic_near_miss_explains_why_not_chosen() -> None:
    profile = AdvertiserProfile(raw_query="single malts", product="single malt whisky", confidence=0.85)
    rec = _rec()
    miss = ScoredPublisher(
        publisher=_publisher("pub_popsip", "Pop & Sip", "drinks"),
        score=0.32,
        confidence=0.4,
        match_strength="weak",
        features=FeatureScores(),
        evidence=FeatureEvidence(product_match="No assortment or audience listed for single malt whisky."),
    )
    result = reason_heuristic(
        profile,
        [rec],
        [miss],
        ExclusionStats(remainder="I left the rest of the catalog out — 12 are a different category, 6 are only a weak or indirect match."),
        "ok",
    )
    assert result.near_misses[0].explanation == (
        "Pop & Sip — close on the drinks shelf, but not a stronger fit than the names above."
    )
    assert "No assortment" not in result.near_misses[0].explanation
    text = render_text(profile, [rec], result, "ok")
    assert "• Pop & Sip — close on the drinks shelf, but not a stronger fit than the names above." in text
    assert text.lower().count("no assortment") == 1
