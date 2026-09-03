from app.graph import catalog
from app.ranking import (
    audience_fit,
    economic_fit,
    insufficient_signal,
    match_confidence,
    score_publishers,
    select_recommendations,
)
from app.retrieval import HashEmbedder, InMemoryPublisherRetriever
from app.schemas import AdvertiserProfile, AudienceHint
from app.understand import extract_heuristic, extract_profile, validate_profile


def _rank(profile: AdvertiserProfile):
    retriever = InMemoryPublisherRetriever(catalog(), embedder=HashEmbedder())
    all_hits = retriever.retrieve_all(profile)
    scored = score_publishers(profile, all_hits[: retriever.pool_size])
    recs, near, stats = select_recommendations(scored, all_hits[retriever.pool_size :])
    return recs, near, stats, scored


def test_single_malt_surfaces_swiftcart_without_whisky_audience_claim() -> None:
    profile = extract_heuristic("We sell a wide range of single malts.")
    assert profile.category == "alcohol"
    assert profile.product
    recs, near, stats, _ = _rank(profile)
    assert recs
    assert recs[0].publisher_name == "Swiftcart"
    product_note = recs[0].evidence.product_match.lower()
    assert "no assortment" in product_note
    assert "single malt" in product_note
    evidence_blob = " ".join(recs[0].evidence.model_dump().values()).lower()
    assert "whisky audience" not in evidence_blob
    assert "not used as a positive claim" in recs[0].evidence.audience_fit.lower()
    names = {m.publisher.name for m in near}
    assert stats.out_of_topic + stats.weak_indirect + stats.near_miss >= 10
    assert "Pop & Sip" in names or recs[0].publisher_name == "Swiftcart"


def test_dog_food_prefers_pet_food_publishers() -> None:
    profile = extract_heuristic("premium senior dog food")
    recs, _, _, _ = _rank(profile)
    names = [r.publisher_name for r in recs]
    assert {"Pawline", "Ruffco"} & set(names)
    assert names[0] in {"Pawline", "Ruffco"}


def test_functional_drink_prefers_pop_and_sip() -> None:
    profile = extract_heuristic("non-alcoholic functional drink")
    recs, _, _, _ = _rank(profile)
    assert recs[0].publisher_name == "Pop & Sip"


def test_bedding_prefers_northbed() -> None:
    profile = extract_heuristic("linen bedding")
    recs, _, _, _ = _rank(profile)
    assert recs[0].publisher_name == "Northbed"


def test_dental_saas_is_not_forced_into_wellness() -> None:
    profile = extract_heuristic("B2B SaaS for dental practices")
    assert profile.category == "software"
    assert any("shelf" in a.lower() for a in profile.ambiguities)
    recs, _, stats, scored = _rank(profile)
    names = {r.publisher_name for r in recs}
    assert "Daily Form" not in names
    assert not recs or recs[0].match_strength == "weak"
    assert stats.out_of_topic >= 5
    assert all("category_mismatch" in row.features.penalty_reasons or row.score < 0.5 for row in scored[:3])
    daily_hit = next(
        c
        for c in InMemoryPublisherRetriever(catalog(), embedder=HashEmbedder()).retrieve_all(profile)
        if c.publisher.name == "Daily Form"
    )
    daily = score_publishers(profile, [daily_hit])[0]
    assert "category_mismatch" in daily.features.penalty_reasons
    assert not daily.eligible


def test_year_is_not_a_price() -> None:
    profile = extract_heuristic("small-batch candles founded in 2020")
    assert profile.price_position == "unknown"
    assert extract_heuristic("$1,200 custom leather handbags").price_position == "luxury"
    # The $ sigil disambiguates a two-digit price from a year or zip.
    assert extract_heuristic("$40 candles").price_position == "budget"
    assert extract_heuristic("$75 candles").price_position == "mid"
    assert extract_heuristic("candles, est 2020, zip 94107").price_position == "unknown"


def test_luxury_handbag_penalizes_low_aov() -> None:
    profile = extract_heuristic("$1,200 custom leather handbags")
    assert profile.price_position == "luxury"
    pubs = {p.name: p for p in catalog()}
    assert economic_fit(profile, pubs["Swiftcart"]) < economic_fit(profile, pubs["Linden Park"])


def test_score_and_confidence_are_separate() -> None:
    profile = AdvertiserProfile(
        raw_query="dog food",
        category="pet",
        subcategory="pet_food",
        product="dog food",
        keywords=["dog food", "pet"],
        confidence=0.35,
    )
    recs, _, _, scored = _rank(profile)
    top = scored[0]
    assert top.score > 0.55
    assert top.confidence < top.score
    assert top.confidence < 0.5
    public = recs[0].to_public()
    assert public["score"] != public["confidence"]
    assert public["confidence"] < public["score"]


def test_audience_conflict_is_flagged() -> None:
    profile = AdvertiserProfile(
        raw_query="workwear for women 50-70",
        category="apparel",
        subcategory="workwear",
        product="workwear",
        keywords=["workwear", "apparel"],
        audience=AudienceHint(age_range="50-70", gender="women"),
        confidence=0.8,
    )
    pubs = {p.name: p for p in catalog()}
    young, young_conflict = audience_fit(profile, pubs["Velvetline"])
    older, older_conflict = audience_fit(profile, pubs["Linden Park"])
    assert young < older
    assert young_conflict
    assert not older_conflict


def test_vague_profile_has_low_confidence() -> None:
    profile = extract_heuristic("We help people feel better.")
    assert profile.product is None
    assert profile.category is None
    assert profile.confidence < 0.45
    assert profile.ambiguities


def test_whisky_is_not_parsed_as_ski_outerwear() -> None:
    """`outerwear|ski` used to match the 'ski' inside 'whisky'."""
    for query in ("whisky", "single malt whisky", "We bottle highland whisky.", "bourbon whiskey"):
        profile = extract_heuristic(query)
        assert profile.category == "alcohol", query
        assert profile.subcategory == "whisky", query
        assert profile.product != "backcountry ski outerwear", query
        assert "ski" not in (profile.product or ""), query
        assert "outerwear" not in (profile.product or ""), query
    assert extract_heuristic("whisky").product == "whisky"
    assert extract_heuristic("single malt whisky").product == "single malt whisky"


def test_ski_outerwear_still_extracts_as_apparel() -> None:
    profile = extract_heuristic("technical backcountry ski outerwear")
    assert profile.category == "apparel"
    assert profile.subcategory == "outerwear"
    assert "ski" in (profile.product or "")


def test_empty_query_does_not_invent_a_product() -> None:
    for raw in ("", "   "):
        profile = extract_profile(raw)
        assert profile.product is None
        assert profile.category is None
        assert profile.confidence <= 0.05
    cleaned = validate_profile(
        AdvertiserProfile(
            raw_query="x",
            category="",
            subcategory="",
            product="",
            business_model="",
            confidence=0.9,
        )
    )
    assert cleaned.category is None
    assert cleaned.subcategory is None
    assert cleaned.product is None
    assert cleaned.business_model is None


def test_unstated_audience_and_price_stay_neutral() -> None:
    pubs = {p.name: p for p in catalog()}
    profile = AdvertiserProfile(raw_query="dog food", category="pet", product="dog food", confidence=0.8)
    score, conflict = audience_fit(profile, pubs["Velvetline"])
    assert score == 0.5
    assert not conflict
    assert economic_fit(profile, pubs["Swiftcart"]) == 0.5


def test_select_recommendations_empty_catalog_gap() -> None:
    recs, near, stats = select_recommendations([], [])
    assert recs == []
    assert near == []
    assert stats.near_miss == 0


def test_idk_does_not_invent_a_product() -> None:
    profile = extract_heuristic("idk just try it")
    assert profile.product is None
    assert profile.audience is None
    assert profile.price_position == "unknown"
    assert profile.confidence < 0.3


def test_already_clarified_still_needs_a_product() -> None:
    empty = AdvertiserProfile(raw_query="idk just try it", confidence=0.2)
    assert insufficient_signal(empty, already_clarified=True)
    mid = AdvertiserProfile(raw_query="idk just try it", confidence=0.4)
    assert not insufficient_signal(mid, already_clarified=True)
    filled = AdvertiserProfile(raw_query="idk just try it", product="dog food", confidence=0.2)
    assert not insufficient_signal(filled, already_clarified=True)


def test_confidence_drops_when_only_broad_or_missing_product() -> None:
    broad = AdvertiserProfile(
        raw_query="alcohol",
        category="alcohol",
        product=None,
        keywords=["alcohol"],
        confidence=0.9,
    )
    retriever = InMemoryPublisherRetriever(catalog(), embedder=HashEmbedder())
    features = score_publishers(broad, retriever.retrieve_all(broad)[: retriever.pool_size])[0].features
    vague = AdvertiserProfile(raw_query="alcohol", category="alcohol", product=None, confidence=0.9)
    specific = AdvertiserProfile(
        raw_query="single malt",
        category="alcohol",
        product="single malt whisky",
        confidence=0.9,
    )
    assert match_confidence(vague, features) < match_confidence(specific, features)
