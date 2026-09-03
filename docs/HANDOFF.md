# Handoff — Disco campaign builder

Phase 1 publisher recommendation is implemented. Matching is not a placeholder. Do not implement matching in `agents.py`.

Repo: `/Users/pratik_behera/PROJECTS/disco-assignment`  
Assignment pack: `disco-takehome-candidate/` (README + glossary + original `data/`). Live catalog copies: `backend/app/data/`.  
Updated: 2026-09-04 (post-review: 45 tests, `retrieve()` removed, `$` 2–5 digit prices, LLM fallback warnings).

---

## What this is

Advertiser types one sentence. System returns ranked publishers from a 20-row catalog, with why / near misses / grouped remainder. Disco is a **retail media network**: publishers are retailers (or retailer-like) that sell ad space on their properties.

**Hard rule:** Python ranks and scores. The model writes language only. No scores, ranks, bids, or budgets from an LLM.

**User working style:** implement in-chat. Do not spawn orc / explore / review agents unless asked. Do not commit unless asked. Do not print `.env` secrets. Do not start personas unless the user asks.

---

## Cross-links

| Path | Role |
|---|---|
| [`backend/app/agents.py`](../backend/app/agents.py) | Entry: `run()` → `get_graph().invoke(...)`. Returns `AgentResult` (`text` / `question` / `chosen`). |
| [`backend/app/graph.py`](../backend/app/graph.py) | LangGraph: parse → validate → retrieve → score → constrain → select → reason. |
| [`backend/app/understand.py`](../backend/app/understand.py) | Advertiser extract: LLM structured parse or conservative heuristic. |
| [`backend/app/retrieval.py`](../backend/app/retrieval.py) | `retrieve_all()` + `pool_size` slice. Protocol has no `retrieve()`. |
| [`backend/app/ranking.py`](../backend/app/ranking.py) | Deterministic score, confidence, eligibility, top-N, near misses. |
| [`backend/app/reason.py`](../backend/app/reason.py) | Copy only. Does not re-rank. |
| [`backend/app/llm.py`](../backend/app/llm.py) | Prompt loader + optional OpenAI client. Ranking never goes through here. |
| [`backend/app/schemas.py`](../backend/app/schemas.py) | Phase 1 types (`AdvertiserProfile`, candidates, recommendations). |
| [`backend/app/main.py`](../backend/app/main.py) | FastAPI SSE only. Calls `app.agents.run`. |
| [`backend/app/data.py`](../backend/app/data.py) | Loads publishers (20) and examples. Personas JSON is on disk, not loaded. |
| [`prompts/advertiser_understanding.md`](../prompts/advertiser_understanding.md) | Extract only supported facts. |
| [`prompts/publisher_reasoning.md`](../prompts/publisher_reasoning.md) | Business-relevance copy from already-ranked evidence. |
| [`docs/RANKING.md`](./RANKING.md) | Scale / hosting notes. |
| [`frontend/src/api.ts`](../frontend/src/api.ts) | SSE reader for `POST /api/run/stream`. |
| [`frontend/src/App.tsx`](../frontend/src/App.tsx) | Chat UI; clarify answer reuses `thread_id`. |

Deleted (not live): leftover `graph/` and `llm/` packages; unused prompts (`clarify.md`, `creative.md`, `extract_brief.md`, `orchestrator.md`, `refine_brief.md`, `why_personas.md`, `why_publishers.md`).

---

## Phase 1 — What shipped (verified 2026-09-04)

Pipeline:

```
query → understand (LLM or heuristic)
     → InMemoryPublisherRetriever
     → deterministic rank (score ≠ confidence)
     → reason (copy only)
```

Entry: `app.agents.run()` → existing SSE in `main.py`.

**Live API** (`backend/app/main.py`): `/`, `/api/health`, `/api/examples`, `POST /api/run/stream` (SSE `stage` / `token` / `clarify` / `done` / `error`). The Vite chat calls it through the dev proxy.

**Graph nodes** (`graph.py`): `parse_advertiser` → `validate_profile` → `retrieve_publishers` → `score_publishers` → `apply_constraints` → `select_recommendations` → `reason_about_matches`.

**Understand:** `extract_profile()` uses `parse_structured` + `advertiser_understanding.md` when `llm_enabled()`; otherwise `extract_heuristic()` (phrase table in `understand.py`). `DISCO_FORCE_HEURISTIC=1` disables the model (tests set this in `backend/tests/conftest.py`). Runtime LLM flag: `LLM_API_KEY` or `OPENAI_API_KEY`. `/api/health` reports `llm` from `LLM_API_KEY` only. Heuristic `_PRICE` treats `$` + 2–5 digits as a price (`$40` → budget); years/zips without `$` or thousands-commas stay unknown. LLM extract failure logs a warning and falls back to heuristic.

**Retrieve:** Live path is `retrieve_all()` then a `pool_size` slice only. `InMemoryPublisherRetriever.retrieve_all()` scores all 20 rows; `graph.retrieve_publishers` takes `ranked[:engine.pool_size]` (default 10) as candidates and the rest as rejected. `PublisherRetriever` protocol is `pool_size` + `retrieve_all` — `retrieve()` was removed. Embedder is `OpenAIEmbedder` if LLM is on, else `HashEmbedder`. Protocol is the documented swap point for a later BM25 + HNSW → RRF implementation.

**Rank:** `score` = weighted evidence (category / subcategory / product / keyword / semantic / audience / economic / behavioral / business-model, then penalties). `confidence` = how complete/trustworthy that evidence is (`match_confidence`). They are separate fields. Top-N = 4. Fallback if nothing is eligible: up to 2 least-wrong rows marked `weak`, never a `category_mismatch` row.

**Reason:** `reason_about_matches()` writes headlines / why / caveats / remainder. Heuristic path always available; LLM path uses `publisher_reasoning.md` and the already-ranked payload only. LLM reasoning failure logs a warning and falls back to heuristic copy. `render_text()` is the SSE reply: one newline per line, bullets for why / caveat / near miss, no blank-line padding. Remainder is always `ExclusionStats.remainder` (`Remaining publishers: N out of topic, N weak/indirect, N near miss.`), not an LLM paragraph. Catalog-gap caveat is shown once; “no advertiser audience was stated” is not used as a caveat.

**Clarify resume:** `main.py` keeps `thread_id → original query` in a bounded in-process dict (`_PENDING_MAX` 256). A reload or a second worker drops it and resume returns **400**; the client starts over.

**Frontend:** `App.tsx` splits the assistant reply on `\n` and renders each line (`leading-5`, no `whitespace-pre-wrap`). Headlines with ` — ` are medium weight; bullets are indented; `Near misses` / `Remaining …` are muted with a small top margin. SSE contract is unchanged (`stage` / `token` / `clarify` / `done` / `error`). `chosen` is in the `done` payload and unused by the UI.

### Run

```bash
cd backend && uvicorn app.main:app --reload --app-dir .
cd frontend && npm run dev   # http://127.0.0.1:5173
```

Set `LLM_API_KEY` (or `OPENAI_API_KEY`) in `backend/.env` for extraction and explanation. Without it, the heuristic still ranks. Do not echo `.env` values.

---

## Decisions / constraints

- Do not force `brief.category` to be one of the 10 publisher `category` strings and hard-gate on it. Name the product; walk the catalog as a shelf, then audience.
- If no real shelf: still return a least-wrong row, mark **weak**, and state the catalog gap in the why. Do not invent fit.
- Clarify only when signal is insufficient (`insufficient_signal` in `ranking.py` / `understand.py`). Resume requires `thread_id`.
- Ranking weights in `ranking.py` are initial heuristics, not learned.
- Production retrieve (metadata filter + BM25 + HNSW → RRF) is documented, not built. Do not add ANN for 20 rows.
- LangGraph is the live orchestrator (`langgraph>=0.6` in `backend/pyproject.toml`). LangChain / LangSmith are not dependencies.
- Personas: `shopper_personas.json` is on disk only. Do not build persona matching / ads unless the user asks.

---

## Historical: why ranking felt wrong

Query ≈ *“We make Cuddle / adult diapers, best quality money can buy.”*

Old path (removed): orchestrator **must pick one catalog category** → often `wellness_dtc` → category hard-gate → only **Daily Form (`pub_012`)** passes → rank #1 is automatic → why-LLM is told to *justify* the top list → invented a wellness story.

Daily Form is vitamins/supplements. It is not a diaper placement. That reaction was correct. There is **no** Phase 1 test for this Cuddle query; do not assume current output.

---

## Next steps

1. Wait for the user to name the next task.
2. Do **not** start personas / ads / campaign config unless they ask.
3. Do **not** re-implement matching in `agents.py`.
4. Do **not** commit or push unless they ask.

---

## Verification (2026-09-04)

- `cd backend && .venv/bin/pytest -q` — **45 passed** (`test_app.py`, `test_ranking.py`, `test_retrieval.py`, `test_phase1_examples.py`, `test_reason.py`). `test_reason.py` covers compact `render_text()` (no `\n\n`, `•` bullets, remainder last, catalog-gap once) and drops the “no advertiser audience” caveat.
- `cd frontend && npm run build` — exit 0 (test-ui).
- Frontend: `App.tsx` line renderer is part of Phase 1. Re-run a query to see compact copy; an older chat bubble still shows the previous wording.

Use `.venv/bin/pytest`, never bare `pytest`.

---

## Do not

- Tell the next agent to implement matching in `agents.py`.
- Start personas unless the user asks.
- Let the LLM rank, invent scores, or pick winners from the full 20-row list.
- Treat “closest catalog category” as the product identity.
- Commit or push unless the user asks.
