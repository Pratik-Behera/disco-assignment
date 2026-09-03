from app.graph import build_graph, catalog
from app.retrieval import (
    CANDIDATE_POOL_SIZE,
    HashEmbedder,
    InMemoryPublisherRetriever,
    PublisherRetriever,
    cosine_similarity,
    keyword_match,
    parts,
)
from app.schemas import AdvertiserProfile, FeatureScores, Publisher, PublisherCandidate


def test_search_text_is_generated_from_json_fields() -> None:
    swiftcart = next(p for p in catalog() if p.name == "Swiftcart")
    text = swiftcart.search_text()
    assert "Publisher: Swiftcart" in text
    assert "Category: instant_delivery" in text
    assert "alcohol" in text
    assert "AOV: $28" in text
    assert "Late-night" in text


def test_retriever_returns_a_candidate_pool_not_the_whole_catalog() -> None:
    profile = AdvertiserProfile(
        raw_query="single malts",
        category="alcohol",
        subcategory="whisky",
        product="single malt whisky",
        keywords=["single malt", "whisky", "alcohol"],
        confidence=0.85,
    )
    retriever = InMemoryPublisherRetriever(catalog(), embedder=HashEmbedder())
    as_protocol: PublisherRetriever = retriever
    full = as_protocol.retrieve_all(profile)
    pool = full[: as_protocol.pool_size]
    assert 1 <= len(pool) <= CANDIDATE_POOL_SIZE
    assert len(pool) == min(retriever.pool_size, len(catalog()))
    assert len(full) == 20
    assert all(isinstance(c, PublisherCandidate) and c.publisher.id for c in pool)
    assert pool[0].publisher.name == "Swiftcart"
    assert pool[0].retrieval_score >= pool[-1].retrieval_score


def test_retrieve_respects_custom_pool_size_and_empty_catalog() -> None:
    profile = AdvertiserProfile(raw_query="dog food", category="pet", product="dog food", confidence=0.8)
    small = InMemoryPublisherRetriever(catalog(), embedder=HashEmbedder(), pool_size=3)
    assert len(small.retrieve_all(profile)[: small.pool_size]) == 3
    assert InMemoryPublisherRetriever([], embedder=HashEmbedder()).retrieve_all(profile) == []
    vague = AdvertiserProfile(raw_query="idk just try it", confidence=0.18)
    bounded = InMemoryPublisherRetriever(catalog(), embedder=HashEmbedder()).retrieve_all(vague)
    assert 1 <= len(bounded[:CANDIDATE_POOL_SIZE]) <= CANDIDATE_POOL_SIZE


def test_protocol_shape_is_profile_to_candidates() -> None:
    retriever: InMemoryPublisherRetriever = InMemoryPublisherRetriever(
        catalog()[:3], embedder=HashEmbedder()
    )
    out = retriever.retrieve_all(AdvertiserProfile(raw_query="pet food", category="pet", confidence=0.7))
    assert all(c.publisher.id for c in out)


def test_build_graph_accepts_a_non_memory_retriever() -> None:
    pubs = catalog()

    class StubRetriever:
        pool_size = 2

        def retrieve_all(self, profile: AdvertiserProfile) -> list[PublisherCandidate]:
            return [
                PublisherCandidate(
                    publisher=p,
                    retrieval_score=0.4,
                    features=FeatureScores(),
                )
                for p in pubs
            ]

    state = build_graph(StubRetriever()).invoke(
        {"raw_query": "premium senior dog food", "clarification": None}
    )
    assert state["status"] == "ok"
    assert isinstance(state.get("text"), str)


def test_cosine_identical_vectors_is_one() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_rejects_empty_or_mismatched_vectors() -> None:
    assert cosine_similarity([], [1.0]) == 0.0
    assert cosine_similarity([1.0], [1.0, 0.0]) == 0.0
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_parts_treats_whisky_and_whiskey_as_synonyms() -> None:
    assert "whiskey" in parts("whisky")
    assert "whisky" in parts("whiskey")


def test_keyword_match_is_zero_without_needles() -> None:
    assert keyword_match(AdvertiserProfile(raw_query="x", confidence=0.5), catalog()[0]) == 0.0


def test_publisher_from_raw_keeps_catalog_fields() -> None:
    row = catalog()[0].model_dump()
    again = Publisher.from_raw(row)
    assert again.id == catalog()[0].id
