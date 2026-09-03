"""Prompt loader and optional OpenAI client. Ranking never goes through here."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

_PROMPTS = Path(__file__).resolve().parents[2] / "prompts"
T = TypeVar("T", bound=BaseModel)


def load_prompt(name: str) -> str:
    path = _PROMPTS / name
    return path.read_text()


def llm_enabled() -> bool:
    if os.environ.get("DISCO_FORCE_HEURISTIC") == "1":
        return False
    return bool(os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY"))


def _api_key() -> str:
    return os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""


@lru_cache(maxsize=1)
def _client():
    from openai import OpenAI

    return OpenAI(api_key=_api_key())


def chat_model() -> str:
    return os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"


def parse_structured(system: str, user: str, schema: type[T]) -> T:
    completion = _client().chat.completions.parse(
        model=chat_model(),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format=schema,
        temperature=0,
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        raise ValueError("model refused structured parse")
    return parsed


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    response = _client().embeddings.create(
        model=os.environ.get("OPENAI_EMBEDDING_MODEL") or "text-embedding-3-small",
        input=texts,
    )
    by_index = {item.index: item.embedding for item in response.data}
    return [by_index[i] for i in range(len(texts))]
