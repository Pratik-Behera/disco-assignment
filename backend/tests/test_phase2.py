import pytest

import app.graph as graph_mod
from app.agents import run
from app.creative import validate_creatives
from app.graph import build_graph
from app.missing import analyze_missing, audience_question
from app.personas import PERSONA_WEIGHTS, match_personas
from app.schemas import AdvertiserProfile, CreativeVariant, MissingQuestion
from app.understand import extract_heuristic


def test_graph_fans_out_after_parse() -> None:
    """Required missing first; rank and personas fan out only after that."""
    graph = build_graph().get_graph()
    outgoing: dict[str, set[str]] = {}
    incoming: dict[str, set[str]] = {}
    for edge in graph.edges:
        outgoing.setdefault(edge.source, set()).add(edge.target)
        incoming.setdefault(edge.target, set()).add(edge.source)
    assert outgoing["parse_advertiser"] == {"analyze_missing"}
    assert outgoing["ready_to_place"] == {"rank_publishers", "match_personas"}
    assert incoming["assemble_result"] == {"rank_publishers", "match_personas"}
    assert outgoing["assemble_result"] == {"creative_generation", "__end__"}
    assert outgoing["creative_generation"] == {"validate_creatives"}
    assert outgoing["validate_creatives"] == {"__end__"}


def test_required_missing_has_no_skip() -> None:
    profile = extract_heuristic("We help people feel better.")
    question = analyze_missing(profile)
    assert question is not None
    assert question.field == "product"
    assert question.importance == "required"
    assert question.allow_skip is False
    assert question.quick_replies


def test_useful_audience_question_allows_skip() -> None:
    profile = extract_heuristic("We sell a wide range of single malts.")
    matches = match_personas(profile)
    question = audience_question(profile, matches)
    assert question is not None
    assert question.field == "target_audience"
    assert question.importance == "useful"
    assert question.allow_skip is True
    assert question.quick_replies
    assert analyze_missing(profile) is None


def test_skip_does_not_reask_audience() -> None:
    profile = extract_heuristic("We sell a wide range of single malts.")
    matches = match_personas(profile)
    assert audience_question(profile, matches, skipped_fields=["target_audience"]) is None


def test_vague_run_is_required_question_only() -> None:
    result = run("We help people feel better.")
    assert result.question
    assert result.question_meta
    assert result.question_meta["importance"] == "required"
    assert result.question_meta["allow_skip"] is False
    assert result.text is None
    assert not result.chosen
    assert not result.creatives
    assert "Ads" not in (result.text or "")


def test_required_question_does_not_spend_a_creative_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A required clarify is the whole reply, so ads must not be generated and dropped."""
    calls: list[int] = []

    def spy(*_args: object, **_kwargs: object) -> list:
        calls.append(1)
        return []

    monkeypatch.setattr(
        graph_mod,
        "analyze_missing",
        lambda *_a, **_k: MissingQuestion(
            field="target_audience",
            importance="required",
            question="Who is this mainly for?",
        ),
    )
    monkeypatch.setattr(graph_mod, "generate_creatives", spy)
    result = run("We sell a wide range of single malts.")
    assert result.question == "Who is this mainly for?"
    assert result.text is None
    assert not calls


def test_single_malt_ranks_and_offers_skippable_followup() -> None:
    result = run("We sell a wide range of single malts.")
    assert result.chosen
    assert result.chosen[0]["publisher_name"] == "Swiftcart"
    assert result.question is None
    assert result.question_meta
    assert result.question_meta["allow_skip"] is True
    assert result.question_meta["field"] == "target_audience"
    assert result.personas
    assert not result.creatives
    assert result.text
    assert "Ads" not in (result.text or "")
    skipped = run(
        "We sell a wide range of single malts.",
        asked_fields=["target_audience"],
        skipped_fields=["target_audience"],
    )
    assert skipped.creatives
    assert skipped.question_meta is None
    assert "score" not in (result.text or "").lower()
    assert "required" not in (result.text or "").lower()


def test_free_text_answer_updates_profile_without_restart() -> None:
    result = run(
        "We sell a wide range of single malts.",
        answers=[{"field": "target_audience", "value": "parents in their 40s"}],
        asked_fields=["target_audience"],
    )
    assert result.question is None
    assert result.question_meta is None
    assert result.chosen
    assert result.chosen[0]["publisher_name"] == "Swiftcart"


def test_whisky_personas_are_not_pet_parents() -> None:
    """Catalog personas never name alcohol; related affinities must still surface gifters."""
    profile = extract_heuristic("We sell a wide range of single malts.")
    matches = match_personas(profile)
    names = {row.persona_name for row in matches}
    assert {"The Gifter", "The Affluent Classic"} & names
    assert "The Pet Parent" not in names
    chips = audience_question(profile, matches)
    assert chips is not None
    assert "Pet Parent" not in chips.quick_replies
    assert {"Gifter", "Affluent Classic"} & set(chips.quick_replies)


def test_candle_gifts_ranks_gifter_and_skips_the_shopper_question() -> None:
    profile = extract_heuristic(
        "Small-batch candles poured by hand in Vermont. Natural soy wax, no synthetic fragrances. Mostly bought as gifts."
    )
    matches = match_personas(profile)
    names = [row.persona_name for row in matches]
    assert names[0] == "The Gifter"
    assert "The Wellness Optimizer" not in names
    assert "The Pet Parent" not in names
    assert audience_question(profile, matches) is None
    result = run(
        "Small-batch candles poured by hand in Vermont. Natural soy wax, no synthetic fragrances. Mostly bought as gifts."
    )
    assert result.question_meta is None
    assert result.chosen
    assert "category overlap" not in (result.personas_text or "")


def test_persona_unknown_price_is_not_a_penalty() -> None:
    profile = AdvertiserProfile(
        raw_query="dog food",
        product="dog food",
        category="pet",
        subcategory="pet_food",
        keywords=["dog food", "pet"],
        price_position="unknown",
        confidence=0.8,
    )
    matches = match_personas(profile, weights=PERSONA_WEIGHTS)
    assert 1 <= len(matches) <= 5
    names = {row.persona_name for row in matches}
    assert "The Pet Parent" in names
    assert all("price sensitivity clash" not in row.negative_signals for row in matches)


def test_persona_disinterest_and_price_only_when_known() -> None:
    premium = AdvertiserProfile(
        raw_query="luxury linen",
        product="linen bedding",
        category="home",
        keywords=["linen", "bedding"],
        price_position="luxury",
        confidence=0.85,
    )
    matches = match_personas(premium)
    assert matches
    assert matches[0].score >= 0.2


def test_creatives_differ_by_persona() -> None:
    malt = run(
        "We sell a wide range of single malts.",
        asked_fields=["target_audience"],
        skipped_fields=["target_audience"],
    )
    gift_ids = {c["persona_id"] for c in (malt.creatives or [])}
    assert gift_ids
    dog = run("premium senior dog food")
    assert dog.creatives
    dog_ids = {c["persona_id"] for c in dog.creatives}
    assert gift_ids != dog_ids or {c["headline"] for c in malt.creatives or []} != {
        c["headline"] for c in dog.creatives or []
    }


def test_creatives_carry_publisher_context() -> None:
    result = run("premium senior dog food")
    assert result.chosen
    pub_ids = {row["publisher_id"] for row in result.chosen}
    assert result.creatives
    assert {c["publisher_id"] for c in result.creatives} <= pub_ids


def test_unsupported_claims_are_dropped() -> None:
    profile = AdvertiserProfile(raw_query="dog food", product="dog food", confidence=0.8)
    kept = validate_creatives(
        [
            CreativeVariant(
                angle="quality",
                headline="50% off today",
                body="Guaranteed results for every dog.",
                cta="Shop now",
                publisher_id="pub_pawline",
                persona_id="persona_004",
            ),
            CreativeVariant(
                angle="emotional",
                headline="For the dogs you love",
                body="Food that fits the daily walk.",
                cta="Find yours",
                publisher_id="pub_pawline",
                persona_id="persona_004",
            ),
        ],
        profile,
    )
    assert [item.headline for item in kept] == ["For the dogs you love"]


def test_phase1_ranking_still_picks_pawline_for_dog_food() -> None:
    result = run("premium senior dog food")
    assert result.question is None
    names = {row["publisher_name"] for row in (result.chosen or [])}
    assert {"Pawline", "Ruffco"} & names
