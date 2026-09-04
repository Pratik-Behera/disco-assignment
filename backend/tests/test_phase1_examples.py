import re

from app.agents import run
from app.ranking import insufficient_signal
from app.understand import extract_heuristic

VAGUE = {
    "We help people feel better.",
    "idk just try it",
    "A new kind of thing for moms.",
}

CLEAR = {
    "premium senior dog food": {"Pawline", "Ruffco"},
    "sustainable women's activewear": {"Movewell"},
    "non-alcoholic functional drink": {"Pop & Sip"},
    "technical backcountry ski outerwear": {"Movewell", "Cloudfoot", "Marlowe & Co.", "Linden Park"},
    "refillable cleaning products": {"Swiftcart"},
    "protein bars": {"Pantrygood", "Kitchenly", "Swiftcart"},
    "cat-owner subscription box": {"Tailcrate", "Pawline", "Ruffco"},
    "workout supplements": {"Daily Form"},
    "linen bedding": {"Northbed"},
}


def test_vague_examples_are_insufficient() -> None:
    for query in VAGUE:
        profile = extract_heuristic(query)
        assert insufficient_signal(profile, already_clarified=False), query
        assert profile.product is None, query
        result = run(query)
        assert result.question, query
        assert result.text is None, query
        assert not result.chosen, query


def test_clear_examples_return_ranked_publishers() -> None:
    for query, expected in CLEAR.items():
        result = run(query)
        assert result.question is None, query
        assert result.chosen, query
        names = {row["publisher_name"] for row in result.chosen}
        assert names & expected, f"{query} → {names}"


def test_single_malt_run_mentions_swiftcart_and_groups_the_rest() -> None:
    result = run("We sell a wide range of single malts.")
    assert result.chosen
    assert result.chosen[0]["publisher_name"] == "Swiftcart"
    evidence = result.chosen[0]["evidence"]
    assert all(evidence[key] for key in ("category_match", "product_match", "audience_fit", "behavioral_fit"))
    text = result.text or ""
    assert "Swiftcart" in text
    assert "whisky audience" not in text.lower()
    assert "left the rest of the catalog out" in text.lower()
    assert re.search(
        r"I left the rest of the catalog out — \d+ are a different category, \d+ are only a weak or indirect match\.",
        text,
    )
    assert "\n\n" not in (result.publishers_text or "")
    assert "• " in text
    assert text.lower().count("no assortment") <= 1


def test_b2b_and_candles_do_not_hallucinate_a_perfect_shelf() -> None:
    saas = run("B2B SaaS for dental practices")
    assert saas.question is None
    if saas.chosen:
        assert saas.chosen[0]["match_strength"] == "weak"
    candles = run("small-batch candles")
    assert candles.text
    assert "Northbed" in (candles.text or "") or "Hearthstone" in (candles.text or "") or "gap" in (candles.text or "").lower() or candles.chosen


def test_short_vague_query_still_clarifies() -> None:
    result = run("We help people")
    assert result.question
    assert result.text is None


def test_vague_then_product_clarification_ranks() -> None:
    result = run("idk just try it", clarification="premium senior dog food")
    assert result.question is None
    assert result.chosen
    names = {row["publisher_name"] for row in result.chosen}
    assert {"Pawline", "Ruffco"} & names


def test_cuddle_adult_diapers_is_not_forced_into_daily_form() -> None:
    """Catalog gap / clarify is fine; the old wellness_dtc → Daily Form #1 path is not."""
    query = "We make Cuddle / adult diapers, best quality money can buy."
    profile = extract_heuristic(query)
    assert profile.category not in {"wellness", "wellness_dtc"}
    assert profile.product != "workout supplements"

    result = run(query)
    chosen = result.chosen or []
    if chosen:
        top = chosen[0]
        if top["publisher_id"] == "pub_012":
            assert top["match_strength"] == "weak"
            assert "directly overlaps" not in top["evidence"]["category_match"].lower()
        else:
            assert top["match_strength"] == "weak"
    else:
        assert result.question
        assert result.text is None
        assert not chosen


def test_whisky_queries_do_not_rank_as_ski_outerwear() -> None:
    ski = {"Movewell", "Cloudfoot", "Marlowe & Co.", "Linden Park"}
    for query in ("whisky", "single malt whisky"):
        result = run(query)
        assert result.question is None, query
        assert result.chosen, query
        assert result.chosen[0]["publisher_name"] not in ski, query
        assert result.chosen[0]["publisher_name"] == "Swiftcart", query
