"""Publisher retrieval: worth-considering pool, not the final ranking.

InMemoryPublisherRetriever scans the current catalog. The Protocol is the
swap point for a later BM25 + HNSW → RRF implementation — ranking/reasoning
should keep consuming PublisherCandidate the same way.

# ponytail: brute-force over all rows. Replace the retriever, not the ranker,
# when the catalog grows past ~10k.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from pathlib import Path
from typing import Protocol

from app.schemas import AdvertiserProfile, FeatureScores, Publisher, PublisherCandidate

TOKEN = re.compile(r"[a-z0-9]+")
CANDIDATE_POOL_SIZE = 10
log = logging.getLogger(__name__)

# Retrieval-only mix. Final business weights live in ranking.py.
_RETRIEVAL_WEIGHTS = {
    "category_match": 0.30,
    "subcategory_match": 0.25,
    "product_match": 0.20,
    "keyword_match": 0.15,
    "semantic_similarity": 0.10,
}


def tokenize(text: str) -> list[str]:
    return TOKEN.findall(text.lower())


def norm(text: str) -> str:
    return "_".join(tokenize(text))


def parts(text: str) -> set[str]:
    n = norm(text)
    if not n:
        return set()
    bits = {n, *n.split("_")}
    extra: set[str] = set()
    for bit in bits:
        if bit.endswith("s") and len(bit) > 4:
            extra.add(bit[:-1])
        elif len(bit) > 4:
            extra.add(bit + "s")
        if bit == "whisky":
            extra.add("whiskey")
        elif bit == "whiskey":
            extra.add("whisky")
    return {b for b in bits | extra if len(b) > 1}


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (na * nb)))


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedder:
    """Hashed bag-of-words stand-in when no embedding API is configured."""

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in tokenize(text):
            digest = hashlib.sha256(tok.encode()).digest()
            vec[int.from_bytes(digest[:4], "big") % self.dim] += 1.0
        return vec


class OpenAIEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        from app.llm import embed_texts

        return embed_texts(texts)


class PublisherRetriever(Protocol):
    """Swap point. Ranked best-first; the graph slices pool_size off the front."""

    pool_size: int

    def retrieve_all(self, profile: AdvertiserProfile) -> list[PublisherCandidate]: ...


# Tiny adjacency list — not a product ontology. Exact shelf beats these.
_RELATED_SHELVES = {
    "food": ("groceries", "pantry", "meal_kits"),
    "household": ("household", "home"),
    "alcohol": ("alcohol", "beverages"),
    "beverages": ("beverages", "groceries"),
    "wellness": ("wellness_dtc", "wellness_services", "supplements", "vitamins"),
    "accessories": ("apparel",),
    "home_fragrance": ("home", "gifting"),
    "cleaning": ("household",),
    "outerwear": ("apparel", "activewear"),
}


def structured_matches(profile: AdvertiserProfile, publisher: Publisher) -> tuple[float, float, float]:
    """Category / subcategory / product vs publisher shelf. Specific beats broad."""
    pub_shelf = [publisher.category, *publisher.subcategories]
    pub_norms = {norm(s) for s in pub_shelf}
    pub_parts: set[str] = set()
    for item in pub_shelf:
        pub_parts |= parts(item)

    def against(term: str | None) -> float:
        if not term:
            return 0.0
        n = norm(term)
        if not n:
            return 0.0
        if n in pub_norms:
            return 1.0
        tokens = [p for p in n.split("_") if p]
        long_term = {p for p in tokens if len(p) > 4}
        long_pub = {p for s in pub_norms for p in s.split("_") if len(p) > 4}
        if long_term and long_term & long_pub:
            return 0.75
        if len(tokens) > 1:
            leaf = tokens[-1]
            if len(leaf) > 3 and any(s.split("_")[-1] == leaf for s in pub_norms):
                return 0.85
        term_parts = {p for p in parts(term) if len(p) > 4}
        if term_parts and term_parts <= pub_parts:
            return 0.8
        overlap = term_parts & pub_parts
        if overlap:
            return 0.45 + 0.35 * (len(overlap) / max(len(term_parts), 1))
        related = _RELATED_SHELVES.get(n, ())
        if any(norm(r) in pub_norms or norm(r) in pub_parts for r in related):
            return 0.55
        return 0.0

    return against(profile.category), against(profile.subcategory), against(profile.product)


def keyword_match(profile: AdvertiserProfile, publisher: Publisher) -> float:
    hay = " ".join([publisher.name, publisher.notes]).lower()
    shelf = {norm(publisher.category), *(norm(s) for s in publisher.subcategories)}
    for tag in list(shelf):
        if tag.endswith("s") and len(tag) > 4:
            shelf.add(tag[:-1])
        elif len(tag) > 4:
            shelf.add(tag + "s")
    terms = [*(profile.keywords or []), *(profile.product_attributes or [])]
    if profile.product:
        terms.append(profile.product)
    needles = [t for t in terms if t]
    if not needles:
        return 0.0
    hits = 0
    for term in needles:
        n = norm(term)
        if not n:
            continue
        phrase = n.replace("_", " ")
        if n in shelf or phrase in hay or n in hay:
            hits += 1
    return hits / len(needles)


def profile_text(profile: AdvertiserProfile) -> str:
    audience = ""
    if profile.audience:
        audience = " ".join(
            x
            for x in [
                profile.audience.age_range,
                profile.audience.gender,
                profile.audience.income,
                *profile.audience.interests,
            ]
            if x
        )
    bits = [
        profile.product,
        profile.category,
        profile.subcategory,
        " ".join(profile.product_attributes),
        " ".join(profile.keywords),
        audience,
        profile.price_position if profile.price_position != "unknown" else "",
        profile.business_model or "",
        " ".join(profile.geography),
        profile.raw_query,
    ]
    return " ".join(b for b in bits if b).strip() or profile.raw_query


def _retrieval_score(features: FeatureScores) -> float:
    total = sum(
        getattr(features, name) * weight for name, weight in _RETRIEVAL_WEIGHTS.items()
    )
    return max(0.0, min(1.0, total))


def _embed_cache_path() -> Path:
    override = os.environ.get("DISCO_EMBED_CACHE")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[1] / ".cache" / "publisher_embeddings.json"


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _embedding_model() -> str:
    from app.llm import embedding_model

    return embedding_model()


def _read_embed_cache() -> tuple[str, dict[str, dict]]:
    path = _embed_cache_path()
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return "", {}
    if not isinstance(raw, dict):
        return "", {}
    model = raw.get("model") if isinstance(raw.get("model"), str) else ""
    items = raw.get("items")
    if not isinstance(items, dict):
        return model, {}
    clean: dict[str, dict] = {}
    for pub_id, row in items.items():
        if not isinstance(row, dict):
            continue
        digest = row.get("hash")
        vector = row.get("vector")
        if isinstance(digest, str) and isinstance(vector, list) and vector and all(
            isinstance(x, (int, float)) for x in vector
        ):
            clean[str(pub_id)] = {"hash": digest, "vector": [float(x) for x in vector]}
    return model, clean


def _write_embed_cache(model: str, items: dict[str, dict]) -> None:
    path = _embed_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"model": model, "items": items}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(path)


class InMemoryPublisherRetriever:
    def __init__(
        self,
        publishers: list[Publisher],
        embedder: Embedder | None = None,
        pool_size: int = CANDIDATE_POOL_SIZE,
    ) -> None:
        self.publishers = publishers
        self.embedder = embedder or HashEmbedder()
        self.pool_size = pool_size
        self._pub_vectors: list[list[float]] | None = None
        if isinstance(self.embedder, OpenAIEmbedder):
            self._load_disk_vectors()

    def _load_disk_vectors(self) -> None:
        """Fill RAM from disk only. Never calls the embedding API."""
        if not self.publishers:
            self._pub_vectors = []
            return
        model, items = _read_embed_cache()
        if model != _embedding_model():
            return
        vectors: list[list[float]] = []
        for publisher in self.publishers:
            digest = _text_hash(publisher.search_text())
            row = items.get(publisher.id)
            if not row or row["hash"] != digest:
                return
            vectors.append(row["vector"])
        self._pub_vectors = vectors

    def _vectors(self) -> list[list[float]]:
        if self._pub_vectors is not None:
            return self._pub_vectors
        texts = [p.search_text() for p in self.publishers]
        if not isinstance(self.embedder, OpenAIEmbedder):
            self._pub_vectors = self.embedder.embed(texts)
            return self._pub_vectors
        model = _embedding_model()
        disk_model, items = _read_embed_cache()
        if disk_model != model:
            items = {}
        vectors: list[list[float] | None] = [None] * len(self.publishers)
        missing_idx: list[int] = []
        missing_texts: list[str] = []
        for i, (publisher, text) in enumerate(zip(self.publishers, texts)):
            cached = items.get(publisher.id)
            if cached and cached["hash"] == _text_hash(text):
                vectors[i] = cached["vector"]
            else:
                missing_idx.append(i)
                missing_texts.append(text)
        if missing_texts:
            fresh = self.embedder.embed(missing_texts)
            for i, vec in zip(missing_idx, fresh):
                vectors[i] = vec
                items[self.publishers[i].id] = {"hash": _text_hash(texts[i]), "vector": vec}
            keep = {p.id for p in self.publishers}
            try:
                _write_embed_cache(model, {k: v for k, v in items.items() if k in keep})
            except OSError:
                log.warning("could not write publisher embed cache", exc_info=True)
        self._pub_vectors = [vec or [0.0] for vec in vectors]
        return self._pub_vectors

    def retrieve_all(self, profile: AdvertiserProfile) -> list[PublisherCandidate]:
        query_vec = self.embedder.embed([profile_text(profile)])[0]
        scored: list[PublisherCandidate] = []
        for publisher, pub_vec in zip(self.publishers, self._vectors()):
            cat, sub, prod = structured_matches(profile, publisher)
            kw = keyword_match(profile, publisher)
            sem = cosine_similarity(query_vec, pub_vec)
            features = FeatureScores(
                category_match=round(cat, 4),
                subcategory_match=round(sub, 4),
                product_match=round(prod, 4),
                keyword_match=round(kw, 4),
                semantic_similarity=round(sem, 4),
            )
            scored.append(
                PublisherCandidate(
                    publisher=publisher,
                    retrieval_score=round(_retrieval_score(features), 4),
                    features=features,
                )
            )
        scored.sort(key=lambda c: c.retrieval_score, reverse=True)
        return scored
