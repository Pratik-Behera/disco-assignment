from datetime import date

import pytest

from app.agents import iter_run, run, run_campaign
from app.campaign import (
    ALLOC_MAX_PCT,
    ALLOC_MIN_PCT,
    allocate_publishers,
    analyze_campaign_missing,
    apply_campaign_answers,
    budget_chip_labels,
    extract_campaign_inputs,
    is_campaign_revision,
    parse_budget_answer,
    validate_campaign_config,
)
from app.schemas import (
    AdvertiserProfile,
    BidRange,
    BidStrategy,
    CampaignConfig,
    CampaignInputs,
    CampaignTargeting,
    PublisherAudience,
    PublisherContext,
)


COMPLETE = (
    "premium senior dog food. $10,000 over 30 days to drive purchases with a $25 CPA."
)


def test_campaign_inputs_merge_ignores_removed_fields() -> None:
    merged = CampaignInputs().merge(extract_campaign_inputs("small-batch candles"))
    assert "geography" not in CampaignInputs.model_fields
    assert merged.objective is None
    assert merged.total_budget_usd is None


def test_complete_campaign_asks_nothing() -> None:
    result = run(COMPLETE)
    assert result.question_meta is None
    assert result.campaign_text
    assert "Campaign objective" in result.campaign_text
    assert "Drive purchases" in result.campaign_text
    assert "$10,000 over 30 days" in result.campaign_text
    assert result.snapshot["campaign"]["objective"] == "conversions"
    assert result.snapshot["campaign"]["duration_days"] == 30
    assert result.snapshot["campaign"]["total_budget_usd"] == 10000


def test_missing_budget_is_required_no_skip() -> None:
    result = run("premium senior dog food. Drive purchases for 30 days.")
    assert result.creatives
    assert result.question_meta
    assert result.question_meta["field"] == "total_budget_usd"
    assert result.question_meta["importance"] == "required"
    assert result.question_meta["allow_skip"] is False
    chips = result.question_meta["quick_replies"]
    assert chips == ["$100", "$500", "$2,000"]
    assert chips == budget_chip_labels()
    assert "type an amount" in result.question_meta["question"].lower()


def test_missing_duration_is_required_no_skip() -> None:
    result = run("premium senior dog food. $10,000 to drive purchases.")
    assert result.question_meta
    assert result.question_meta["field"] == "campaign_duration"
    assert result.question_meta["allow_skip"] is False
    assert result.snapshot["campaign_inputs"]["total_budget_usd"] == 10000
    assert result.snapshot["campaign_inputs"]["duration_days"] is None


def test_budget_without_duration_does_not_assume_30_days() -> None:
    found = extract_campaign_inputs("We have $10k to spend.")
    assert found.total_budget_usd == 10000
    assert found.duration_days is None
    question = analyze_campaign_missing(
        CampaignInputs(objective="conversions", total_budget_usd=10000)
    )
    assert question is not None
    assert question.field == "campaign_duration"
    assert question.allow_skip is False
    assert "10,000" in question.question


def test_missing_optional_allows_skip() -> None:
    result = run("premium senior dog food. $10,000 over 30 days to drive purchases.")
    assert result.question_meta
    assert result.question_meta["field"] == "performance_goal"
    assert result.question_meta["importance"] == "useful"
    assert result.question_meta["allow_skip"] is True


def test_required_campaign_input_has_no_skip() -> None:
    question = analyze_campaign_missing(CampaignInputs())
    assert question is not None
    assert question.field == "campaign_objective"
    assert question.importance == "required"
    assert question.allow_skip is False
    assert "objective_required" not in question.question


def test_natural_duration_parses_about_a_month() -> None:
    assert extract_campaign_inputs("About a month").duration_days == 30
    assert extract_campaign_inputs("For about a month").duration_days == 30
    assert extract_campaign_inputs("two weeks").duration_days == 14
    assert extract_campaign_inputs("30 days").duration_days == 30
    until = extract_campaign_inputs("until the end of November", today=date(2026, 9, 4))
    assert until.duration_days == (date(2026, 11, 30) - date(2026, 9, 4)).days


def test_zero_duration_is_missing_not_a_crash() -> None:
    assert extract_campaign_inputs("0 days").duration_days is None
    assert extract_campaign_inputs("0 weeks").duration_days is None
    assert extract_campaign_inputs("0 months").duration_days is None
    result = run("premium senior dog food. $10,000 over 0 days to drive purchases.")
    assert result.question_meta
    assert result.question_meta["field"] == "campaign_duration"
    answered = apply_campaign_answers(
        CampaignInputs(objective="conversions", total_budget_usd=10000),
        [{"field": "campaign_duration", "value": "0 weeks"}],
    )
    assert answered.duration_days is None
    assert analyze_campaign_missing(answered).field == "campaign_duration"


def test_product_price_is_not_a_campaign_budget() -> None:
    assert extract_campaign_inputs("$40 candles").total_budget_usd is None
    assert extract_campaign_inputs("We have $10k to spend.").total_budget_usd == 10000


def test_typed_budget_answer_accepts_bare_amounts() -> None:
    assert parse_budget_answer("500") == 500
    assert parse_budget_answer("$200") == 200
    assert parse_budget_answer("200 dollars") == 200
    assert parse_budget_answer("2k") == 2000
    assert parse_budget_answer("$2,000") == 2000
    filled = apply_campaign_answers(CampaignInputs(), [{"field": "total_budget_usd", "value": "500"}])
    assert filled.total_budget_usd == 500
    still = apply_campaign_answers(CampaignInputs(), [{"field": "total_budget_usd", "value": "200 dollars"}])
    assert still.total_budget_usd == 200
    assert extract_campaign_inputs("$40 candles").total_budget_usd is None
    assert is_campaign_revision("500")
    assert is_campaign_revision("$100")
    assert not is_campaign_revision("$40 candles")
    first = run("premium senior dog food. Drive purchases for 30 days.")
    assert first.question_meta and first.question_meta["field"] == "total_budget_usd"
    second = run(
        "premium senior dog food. Drive purchases for 30 days.",
        answers=[{"field": "total_budget_usd", "value": "500"}],
    )
    assert second.snapshot["campaign_inputs"]["total_budget_usd"] == 500
    assert second.question_meta is None or second.question_meta["field"] != "total_budget_usd"


def test_budget_change_recalculates_dollars_not_percentages() -> None:
    first = run(COMPLETE)
    campaign = first.snapshot["campaign"]
    pcts = [row["allocation_pct"] for row in campaign["publishers"]]
    second = run_campaign(first.snapshot, raw_update="Actually, let's make it $15k.")
    updated = second.snapshot["campaign"]
    assert updated["total_budget_usd"] == 15000
    assert updated["duration_days"] == 30
    assert [row["allocation_pct"] for row in updated["publishers"]] == pcts
    assert sum(row["allocation_usd"] for row in updated["publishers"]) == 15000
    assert updated["daily_budget_usd"] == 500
    assert first.snapshot["chosen"] == second.snapshot["chosen"]


def test_duration_change_keeps_total_and_percentages() -> None:
    first = run(COMPLETE)
    campaign = first.snapshot["campaign"]
    pcts = [row["allocation_pct"] for row in campaign["publishers"]]
    second = run_campaign(first.snapshot, raw_update="60 days")
    updated = second.snapshot["campaign"]
    assert updated["duration_days"] == 60
    assert updated["total_budget_usd"] == 10000
    assert updated["daily_budget_usd"] == 10000 / 60
    assert [row["allocation_pct"] for row in updated["publishers"]] == pcts


def test_chip_budget_revision_beats_the_answer_that_built_the_plan() -> None:
    """"$500" is a revision the detector accepts, so it must actually move the budget."""
    assert extract_campaign_inputs("$100").total_budget_usd == 100
    assert extract_campaign_inputs("Make it $500").total_budget_usd == 500
    first = run(COMPLETE)
    second = run_campaign(
        first.snapshot,
        answers=[{"field": "total_budget_usd", "value": "$10,000"}],
        raw_update="$500",
    )
    updated = second.snapshot["campaign"]
    assert updated["total_budget_usd"] == 500
    assert sum(row["allocation_usd"] for row in updated["publishers"]) == 500


def test_objective_change_updates_bid_strategy() -> None:
    first = run(COMPLETE)
    assert first.snapshot["campaign"]["bid_strategy"]["type"] == "CPA"
    second = run_campaign(first.snapshot, raw_update="Build awareness")
    assert second.snapshot["campaign"]["objective"] == "awareness"
    assert second.snapshot["campaign"]["bid_strategy"]["type"] == "CPM"
    assert second.snapshot["campaign"]["bid_strategy"]["basis"] == "heuristic"
    assert "Why this setup" in second.campaign_text


def test_targeting_does_not_invent_advertiser_demographics() -> None:
    result = run(COMPLETE)
    targeting = result.snapshot["campaign"]["targeting"]
    if targeting["age_range"]:
        assert targeting["age_range_source"] != "advertiser"
    if targeting["gender"]:
        assert targeting["gender_source"] != "advertiser"
    assert targeting["age_range"] != "37–48"


def test_advertiser_stated_audience_is_used() -> None:
    result = run(
        "premium dog food for women aged 30-45. $10,000 over 30 days to drive purchases with a $25 CPA."
    )
    targeting = result.snapshot["campaign"]["targeting"]
    assert targeting["age_range_source"] == "advertiser"
    assert "30" in (targeting["age_range"] or "")
    assert targeting["gender_source"] == "advertiser"


def test_allocations_reconcile_to_100_and_budget() -> None:
    campaign = run(COMPLETE).snapshot["campaign"]
    pubs = campaign["publishers"]
    assert pubs
    assert sum(row["allocation_pct"] for row in pubs) == 100
    assert sum(row["allocation_usd"] for row in pubs) == campaign["total_budget_usd"]
    assert all(row["allocation_usd"] > 0 for row in pubs)


def test_no_strong_match_does_not_fabricate_allocation() -> None:
    """Weak-only chosen rows must not invent a split — direct call, not catalog-dependent."""
    profile = AdvertiserProfile(
        raw_query="B2B SaaS for dental practices",
        product="B2B SaaS",
        category="software",
        confidence=0.8,
    )
    ctxs = [
        PublisherContext(
            publisher_id="pub_weak_a",
            publisher_name="Weak A",
            category="pet",
            monthly_impressions=1_000_000,
            audience=PublisherAudience(),
        ),
        PublisherContext(
            publisher_id="pub_weak_b",
            publisher_name="Weak B",
            category="food",
            monthly_impressions=2_000_000,
            audience=PublisherAudience(),
        ),
    ]
    chosen = [
        {
            "publisher_id": "pub_weak_a",
            "publisher_name": "Weak A",
            "score": 0.2,
            "confidence": 0.3,
            "match_strength": "weak",
        },
        {
            "publisher_id": "pub_weak_b",
            "publisher_name": "Weak B",
            "score": 0.15,
            "confidence": 0.3,
            "match_strength": "weak",
        },
    ]
    rows, warnings = allocate_publishers(chosen, ctxs, profile, 10000)
    assert rows == []
    assert any("not splitting" in note.lower() for note in warnings)


def test_catalog_all_weak_run_has_empty_publishers() -> None:
    result = run(
        "B2B SaaS for dental practices. $10,000 over 30 days to drive purchases with a $25 CPA."
    )
    strengths = [row.get("match_strength") for row in (result.chosen or [])]
    if not strengths or any(s != "weak" for s in strengths):
        pytest.skip(
            "catalog returned a non-weak match for this query; empty allocation "
            "is asserted in test_no_strong_match_does_not_fabricate_allocation"
        )
    assert result.snapshot["campaign"]["publishers"] == []
    assert "not splitting" in result.campaign_text.lower() or "stronger publisher" in result.campaign_text.lower()


def test_many_publishers_still_reconcile_to_100_and_budget() -> None:
    profile = AdvertiserProfile(raw_query="dog food", product="dog food", category="pet", confidence=0.8)
    ctxs = [
        PublisherContext(
            publisher_id=f"pub_{i:03d}",
            publisher_name=f"Publisher {i}",
            category="pet",
            monthly_impressions=1_000_000 + i * 250_000,
            audience=PublisherAudience(),
        )
        for i in range(8)
    ]
    chosen = [
        {
            "publisher_id": ctx.publisher_id,
            "publisher_name": ctx.publisher_name,
            "score": 0.95 - i * 0.07,
            "confidence": 0.7,
            "match_strength": "strong" if i < 5 else "moderate",
        }
        for i, ctx in enumerate(ctxs)
    ]
    rows, _ = allocate_publishers(chosen, ctxs, profile, 10000)
    assert len(rows) == 8
    assert sum(row.allocation_pct for row in rows) == 100
    assert sum(row.allocation_usd for row in rows) == 10000
    assert all(row.allocation_usd > 0 for row in rows)
    assert all(row.allocation_pct > 0 for row in rows)


def test_allocation_guardrails_keep_shares_in_band() -> None:
    profile = AdvertiserProfile(raw_query="dog food", product="dog food", category="pet", confidence=0.8)
    ctxs = [
        PublisherContext(
            publisher_id="pub_hot",
            publisher_name="Hot",
            category="pet",
            monthly_impressions=5_000_000,
            audience=PublisherAudience(),
        ),
        PublisherContext(
            publisher_id="pub_cold",
            publisher_name="Cold",
            category="pet",
            monthly_impressions=200_000,
            audience=PublisherAudience(),
        ),
    ]
    chosen = [
        {
            "publisher_id": "pub_hot",
            "publisher_name": "Hot",
            "score": 0.99,
            "confidence": 0.9,
            "match_strength": "strong",
        },
        {
            "publisher_id": "pub_cold",
            "publisher_name": "Cold",
            "score": 0.01,
            "confidence": 0.4,
            "match_strength": "moderate",
        },
    ]
    rows, _ = allocate_publishers(chosen, ctxs, profile, 10000)
    pcts = [row.allocation_pct for row in rows]
    assert len(pcts) == 2
    assert sum(pcts) == 100
    assert sum(row.allocation_usd for row in rows) == 10000
    # Integer rounding can move a share by 1pt; the band itself must still hold.
    assert min(pcts) >= ALLOC_MIN_PCT * 100 - 1
    assert max(pcts) <= ALLOC_MAX_PCT * 100 + 1


def test_validation_fixes_drift_and_bid_mismatch() -> None:
    config = CampaignConfig(
        objective="traffic",
        total_budget_usd=1000,
        duration_days=10,
        daily_budget_usd=99,
        targeting=CampaignTargeting(),
        publishers=[],
        bid_strategy=BidStrategy(type="CPM", starting_bid_range=BidRange(min=1, max=2), basis="heuristic"),
        confidence=0.5,
    )
    fixed = validate_campaign_config(
        config,
        CampaignInputs(objective="traffic", total_budget_usd=1000, duration_days=10),
        [],
    )
    assert fixed.daily_budget_usd == 100
    assert fixed.bid_strategy.type == "CPC"
    assert fixed.bid_strategy.basis == "heuristic"


def test_revision_detector() -> None:
    assert is_campaign_revision("Actually, let's make it $15k.")
    assert is_campaign_revision("60 days")
    assert is_campaign_revision("Build awareness")
    assert is_campaign_revision("Drive traffic")
    assert is_campaign_revision("Drive purchases")
    assert not is_campaign_revision("premium senior dog food")


def test_product_sentences_are_not_campaign_revisions() -> None:
    """New product copy must not look like a campaign edit, even if extract fires."""
    candles = extract_campaign_inputs("candles for 30 days")
    assert candles.duration_days == 30
    assert candles.objective is None
    flagged = [
        text
        for text in (
            "candles for 30 days",
            "drive traffic to our site",
            "We sell candles on our website",
            "holiday traffic at our store",
        )
        if is_campaign_revision(text)
    ]
    assert flagged == []


def test_objective_not_stolen_from_product_copy() -> None:
    assert extract_campaign_inputs("premium senior dog food").objective is None
    stolen = {
        text: extract_campaign_inputs(text).objective
        for text in (
            "We sell candles on our website",
            "holiday traffic at our store",
        )
        if extract_campaign_inputs(text).objective is not None
    }
    assert stolen == {}
    assert extract_campaign_inputs("Drive traffic").objective == "traffic"
    assert extract_campaign_inputs("Drive purchases").objective == "conversions"
    assert extract_campaign_inputs("Build awareness").objective == "awareness"
    assert extract_campaign_inputs("$10k to drive purchases").objective == "conversions"


def test_iter_run_exhausts_after_campaign_clarify() -> None:
    nodes = [name for name, _ in iter_run("premium senior dog food")]
    assert "campaign_input_analysis" in nodes
    assert nodes[-1] == "campaign_input_analysis"
    assert "build_campaign" not in nodes


def test_single_publisher_gets_100_percent() -> None:
    profile = AdvertiserProfile(raw_query="dog food", product="dog food", category="pet", confidence=0.8)
    ctx = PublisherContext(
        publisher_id="pub_007",
        publisher_name="Pawline",
        category="pet",
        monthly_impressions=4800000,
        audience=PublisherAudience(),
    )
    rows, _ = allocate_publishers(
        [{"publisher_id": "pub_007", "publisher_name": "Pawline", "score": 0.8, "confidence": 0.7, "match_strength": "strong"}],
        [ctx],
        profile,
        10000,
    )
    assert len(rows) == 1
    assert rows[0].allocation_pct == 100
    assert rows[0].allocation_usd == 10000
