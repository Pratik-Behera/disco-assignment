"""Structured types for Phase 1 publisher recommendation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

PricePosition = Literal["budget", "mid", "premium", "luxury", "unknown"]
MatchStrength = Literal["strong", "moderate", "weak"]


class AudienceHint(BaseModel):
    """Only attributes supported by the advertiser text. Missing stays null."""

    age_range: str | None = None
    gender: str | None = None
    income: str | None = None
    interests: list[str] = Field(default_factory=list)


class AdvertiserProfile(BaseModel):
    raw_query: str
    category: str | None = None
    subcategory: str | None = None
    product: str | None = None
    product_attributes: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    audience: AudienceHint | None = None
    price_position: PricePosition = "unknown"
    business_model: str | None = None
    geography: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    ambiguities: list[str] = Field(default_factory=list)


class ExtractedProfile(BaseModel):
    """LLM extract shape. `raw_query` is attached in code, never invented."""

    category: str | None = None
    subcategory: str | None = None
    product: str | None = None
    product_attributes: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    audience: AudienceHint | None = None
    price_position: PricePosition = "unknown"
    business_model: str | None = None
    geography: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    ambiguities: list[str] = Field(default_factory=list)


class PublisherAudience(BaseModel):
    age_skew: str = ""
    gender_split: dict[str, float] = Field(default_factory=dict)
    top_geos: list[str] = Field(default_factory=list)
    income_tier: str = ""


class Publisher(BaseModel):
    id: str
    name: str
    category: str
    subcategories: list[str]
    monthly_impressions: int
    avg_order_value_usd: float
    audience: PublisherAudience
    notes: str = ""

    @classmethod
    def from_raw(cls, row: dict) -> Publisher:
        return cls.model_validate(row)

    def search_text(self) -> str:
        aud = self.audience
        geos = "/".join(aud.top_geos) if aud.top_geos else "unspecified"
        income = f"{aud.income_tier} income" if aud.income_tier else "income unspecified"
        return (
            f"Publisher: {self.name}\n"
            f"Category: {self.category}\n"
            f"Subcategories: {', '.join(self.subcategories)}\n"
            f"Audience: {aud.age_skew}, {income}, {geos}\n"
            f"AOV: ${self.avg_order_value_usd:.0f}\n"
            f"Notes: {self.notes}"
        )


class FeatureEvidence(BaseModel):
    category_match: str = ""
    product_match: str = ""
    audience_fit: str = ""
    behavioral_fit: str = ""


class FeatureScores(BaseModel):
    category_match: float = 0.0
    subcategory_match: float = 0.0
    product_match: float = 0.0
    keyword_match: float = 0.0
    semantic_similarity: float = 0.0
    audience_fit: float = 0.5
    economic_fit: float = 0.5
    behavioral_fit: float = 0.5
    business_model_fit: float = 0.5
    penalty: float = 1.0
    penalty_reasons: list[str] = Field(default_factory=list)


class PublisherCandidate(BaseModel):
    publisher: Publisher
    retrieval_score: float
    features: FeatureScores


class ScoredPublisher(BaseModel):
    publisher: Publisher
    score: float
    confidence: float
    match_strength: MatchStrength
    features: FeatureScores
    evidence: FeatureEvidence
    eligible: bool = True


class ExclusionStats(BaseModel):
    out_of_topic: int = 0
    weak_indirect: int = 0
    near_miss: int = 0
    remainder: str = ""


class Recommendation(BaseModel):
    publisher_id: str
    publisher_name: str
    score: float
    confidence: float
    match_strength: MatchStrength
    evidence: FeatureEvidence

    def to_public(self) -> dict:
        return {
            "publisher_id": self.publisher_id,
            "publisher_name": self.publisher_name,
            "score": round(self.score, 2),
            "confidence": round(self.confidence, 2),
            "match_strength": self.match_strength,
            "evidence": self.evidence.model_dump(),
        }


class PublisherReason(BaseModel):
    publisher_id: str
    headline: str
    why: str
    caveat: str = ""


class NearMissReason(BaseModel):
    publisher_id: str
    publisher_name: str
    explanation: str


class ReasoningResult(BaseModel):
    recommendations: list[PublisherReason] = Field(default_factory=list)
    near_misses: list[NearMissReason] = Field(default_factory=list)
    remainder: str = ""
    clarification: str | None = None
