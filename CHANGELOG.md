# Changelog

Verified record of repository changes. Updated by `change-tracker-agent` from git and code evidence only.

## [Unreleased]

Working tree. Phase 1 publisher recommendation is implemented (`understand` → in-memory retrieve → deterministic rank → reason). Reply copy is compact bullets; chat renders each line. Backend `cd backend && .venv/bin/pytest -q`: 45 passed (this sync).

- Added LangGraph Phase 1 matching behind `app.agents.run()`; ranking stays in Python (`backend/app/{graph,understand,retrieval,ranking,reason,llm,schemas}.py`).
- Added `langgraph` and `openai` dependencies (`backend/pyproject.toml`).
- Added live prompts `prompts/advertiser_understanding.md` and `prompts/publisher_reasoning.md`.
- Added Phase 1 tests for examples, ranking, retrieval, and reason copy (`backend/tests/{conftest,test_phase1_examples,test_ranking,test_retrieval,test_reason}.py`).
- Added `backend/tests/test_reason.py` for compact copy, remainder, near-miss shelf wording, and stripping the unused-audience caveat; dropped dead `PublisherCandidate(evidence=…)` from `test_retrieval.py`.
- Documented the Phase 1 pipeline layout in `README.md`.
- Fixed heuristic `outerwear|ski` matching the substring `ski` inside `whisky`: pattern is now `\bski\b`, plus a whisky phrase and regression tests (`understand.py`, `test_ranking.py`, `test_phase1_examples.py`).
- Simplified the candle/home-fragrance ambiguity check to `subcategory == home_fragrance` (`understand.py`).
- Removed leftover empty `backend/app/graph/` and `backend/app/llm/` packages (old `__pycache__` only) so they cannot shadow `graph.py` / `llm.py`.
- Removed unused old prompts (`extract_brief`, `clarify`, `creative`, `orchestrator`, `refine_brief`, `why_*`), unused `RunStatus`, unused `PublisherCandidate.evidence`, and the unused `reasoning_context` / empty prepare node.
- Ignored `frontend/tsconfig.tsbuildinfo`.
- `main.py` imports FastAPI/pydantic then `app.agents` / `app.data`, then calls `_load_env()`; comment says LLM/LangSmith env is read on first graph invoke. `/api/health` reports `langsmith`. Tests set `LANGSMITH_TRACING=false`.
- Typed `build_graph()` to the `PublisherRetriever` protocol (`retrieve_all`, `pool_size`); dropped unused `retrieve()` from the protocol and `InMemoryPublisherRetriever`. Graph slices `retrieve_all()` by `pool_size`.
- Cleared `chosen` when status is `insufficient_signal`, including after a clarify that still has no product.
- Stopped treating bare years like `2020` as prices; `$` amounts may be 2–5 digits so `$40` is budget, while years/zips without `$` or thousands-commas stay unknown (`understand.py`).
- Restored the catalog-gap ambiguity flag for `software` as well as `home_fragrance`.
- Removed unused `load_personas()`; `shopper_personas.json` stays on disk for Phase 2.
- LLM failures in understand/reason now `log.warning(..., exc_info=True)` instead of a silent fallback.
- Dropped unused `ANTHROPIC_MODEL` from `backend/.env.example`; documented `OPENAI_EMBEDDING_MODEL` and `LANGSMITH_*` placeholders.
- Tightened recommendation copy: `render_text()` uses single-newline bullets; remainder is one grouped line from `ExclusionStats`; catalog-gap caveat is not repeated on the near miss (`reason.py`, `ranking.py`, `prompts/publisher_reasoning.md`).
- Chat assistant replies render one line per `\n` instead of `whitespace-pre-wrap` (`frontend/src/App.tsx`).

## 2026-09-03 — FastAPI shell and chat UI

- Fixed clarify resume silently dropping the advertiser's original query: an unknown or expired `thread_id` now returns 400 instead of running the agent with empty input, and the pending-thread map is bounded at 256 entries (`backend/app/main.py`).
- Fixed `/api/examples` returning the file's `---` rule as an advertiser example and leaking the `1. ` list numbering into the query text (`backend/app/data.py`).
- Stopped streaming raw exception text to the browser on the SSE `error` event; the traceback is logged server-side instead, so a provider auth error cannot carry an API key into the page (`backend/app/main.py`).
- Fixed the SSE token stream collapsing newlines, so a multi-line reply no longer reflows when the final `done` payload lands (`backend/app/main.py`).
- Fixed the frontend leaving a blinking caret under a reply that failed mid-stream, leaving the response body open after an `error` event, and crashing when `/api/examples` returns a non-list (`frontend/src/App.tsx`, `frontend/src/api.ts`).
- Removed the unused `budget_usd` field from the run request and the unused `framer-motion` dependency (`frontend/`).
- Reset the backend to `main.py` (SSE API), `agents.py` (placeholder `run()`), `data.py` (catalog loaders), and 7 API tests; dependencies at that commit were fastapi + uvicorn + pydantic only (`backend/pyproject.toml`).
- Added the Vite + React chat UI: single-column stream, example chips, one-question clarify round (`frontend/src/`).
- Added versioned prompts under `prompts/` (`extract_brief`, `clarify`, `refine_brief`, `creative`, `orchestrator`, `why_*`).
- Added ranking/hosting notes (`docs/RANKING.md`), status (`docs/STATUS.md`), and next-agent handoff (`docs/HANDOFF.md`).

## 2026-09-01 — Take-home candidate pack

- Added Disco take-home materials under `disco-takehome-candidate/` (README, GLOSSARY, `publishers.json`, `shopper_personas.json`, `example_advertisers.txt`).

<!-- last-sync: 2026-09-04, working-tree -->
