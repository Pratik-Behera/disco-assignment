# Changelog

Verified record of repository changes. Updated by `change-tracker-agent` from git and code evidence only.

## [Unreleased]

- OpenAI publisher embeddings persist in `backend/.cache/publisher_embeddings.json` (override `DISCO_EMBED_CACHE`); reload no longer re-embeds all 20 rows (`retrieval.py`, `test_retrieval.py`).
- Persona list drops weak non-overlap names; gift/gifting boosts The Gifter; audience question skips when gifts/gifting already stated (`personas.py`, `missing.py`).
- Chat status line follows the live stage while work is in flight; assistant replies are plain text, not cards (`App.tsx`).
- `iter_run` swallows `GeneratorExit`; SSE keeps draining the graph stream after clarify so clients/LangSmith never see it as an error (`agents.py`, `main.py`, `test_app.py`).
- Removed dead `GraphState.question` and SSE `done.followup`; questions flow via `question_meta` / `clarify` only (`graph.py`, `main.py`, `test_app.py`).
- Staged conversation: required product question first; then publishers, then personas; shopper chips (Skip allowed) before ads. Useful resume is `run_ads` only (`graph.py`, `main.py`, `App.tsx`).
- After a product is clear, LangGraph fans out `rank_publishers` and `match_personas` in one superstep (official multi-edge fan-out), then assemble → one-batch creatives unless a shopper question is pending (`graph.py`).
- Added missing-info, persona scoring, publisher context, and claim-safe creatives (`missing.py`, `personas.py`, `creative.py`, `data.load_personas`, `prompts/{missing_information,ad_creative}.md`).
- Clarify SSE now carries structured question metadata; useful questions include Skip; required questions do not (`main.py`, `App.tsx`).
- Dropped `temperature=0` from `parse_structured` so models that only accept the default temperature can run (`llm.py`).
- Fixed `/api/health` `llm` to use `llm_enabled()` so it accepts `OPENAI_API_KEY` and honors `DISCO_FORCE_HEURISTIC` (`backend/app/main.py`).
- Added `test_phase2.py` (topology, required vs useful, Skip, creatives), embed-cache and SSE stream-exhaust regressions, `test_health_llm_matches_llm_enabled`, Cuddle/adult-diaper catalog-gap regression, and catalog-gap fallback never promoting `category_mismatch` (76 backend tests).
- Documented that matching lives in `graph.py` plus modules (`agents.py` is entry only); README and `.env.example` now mention `OPENAI_API_KEY` and `DISCO_FORCE_HEURISTIC`.
- Required missing-info halts at `halt_required` with `insufficient_signal` and skips rank, personas, and creatives (pinned by `test_required_question_does_not_spend_a_creative_batch`). No product and no category is insufficient on its own, so a skipped required question does not return ranked rows.
- `generate_creatives` no longer validates; the graph `validate_creatives` node is the single gate (`creative.py`). Resume stores the answer on `answers` only, not also as `Clarification` (`main.py`).
- Deleted unused `understand.clarification_question()` and dead `GraphState` keys `candidates`, `rejected`, and `question`.
- Corrected HANDOFF/STATUS: Phase 2 topology, four live prompts (added `missing_information.md` and `ad_creative.md`), `data.py::load_personas()`, and test count 76.
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
- LLM failures in understand/reason now `log.warning(..., exc_info=True)` instead of a silent fallback.
- Dropped unused `ANTHROPIC_MODEL` from `backend/.env.example`; documented `OPENAI_EMBEDDING_MODEL` and `LANGSMITH_*` placeholders.
- Tightened publisher reasoning: `render_text()` drops score/confidence lines and uses single-newline bullets; near-miss and remainder copy read like planner notes; LLM `remainder` falls back to heuristic when empty (`reason.py`, `ranking.py`, `prompts/publisher_reasoning.md`).
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
