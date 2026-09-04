# Handoff — Disco campaign builder

Phase 2 sits on Phase 1 ranking. Matching is not a placeholder and is not in `agents.py`.

Repo: `/Users/pratik_behera/PROJECTS/disco-assignment`  
Assignment pack: `disco-takehome-candidate/` (README + glossary + original `data/`). Live catalog copies: `backend/app/data/`.  
Updated: 2026-09-04 (Phase 2 on Phase 1 ranking. SSE exhausts `iter_run` via `finished` flag. 76 pytest, `npm run build` exit 0).

---

## What this is

Advertiser types one sentence. System returns ranked publishers, matched shopper personas, and 3–5 ad variants, plus at most one follow-up question with quick replies. Disco is a **retail media network**: publishers are retailers (or retailer-like) that sell ad space on their properties.

**Hard rule:** Python ranks and scores. The model writes language only. No scores, ranks, bids, or budgets from an LLM.

**User working style:** implement in-chat. Do not spawn orc / explore / review agents unless asked. Do not commit unless asked (`git-commit-agent` proposes only). Do not print `.env` secrets. Do not start campaign config unless the user asks.

---

## Cross-links

| Path | Role |
|---|---|
| [`backend/app/agents.py`](../backend/app/agents.py) | Entry: `run()` → `get_graph().invoke(...)`. Returns `AgentResult` (`text` / `question` / `question_meta` / `chosen` / `personas` / `creatives`). |
| [`backend/app/graph.py`](../backend/app/graph.py) | LangGraph: parse → missing halt vs **parallel** rank ║ personas → assemble (`reason_about_matches`) → optional ads. |
| [`backend/app/understand.py`](../backend/app/understand.py) | Advertiser extract: LLM structured parse or conservative heuristic. |
| [`backend/app/retrieval.py`](../backend/app/retrieval.py) | `retrieve_all()` + `pool_size` slice. Protocol has no `retrieve()`. |
| [`backend/app/ranking.py`](../backend/app/ranking.py) | Deterministic score, confidence, eligibility, top-N, near misses. |
| [`backend/app/reason.py`](../backend/app/reason.py) | Copy only. Does not re-rank. |
| [`backend/app/llm.py`](../backend/app/llm.py) | Prompt loader + optional OpenAI client. Ranking never goes through here. |
| [`backend/app/schemas.py`](../backend/app/schemas.py) | Shared types (`AdvertiserProfile`, `MissingQuestion`, `PersonaMatch`, `CreativeVariant`, recommendations). |
| [`backend/app/main.py`](../backend/app/main.py) | FastAPI SSE only. Calls `app.agents.iter_run`; exhausts iterator after clarify/done (`finished` flag). |
| [`backend/app/data.py`](../backend/app/data.py) | Loads publishers (20), personas (10), and examples. |
| [`backend/app/missing.py`](../backend/app/missing.py) | One required or useful question. `allow_skip` only when useful. |
| [`backend/app/personas.py`](../backend/app/personas.py) | Scores all 10 personas in Python. Unknown is not mismatch. |
| [`backend/app/creative.py`](../backend/app/creative.py) | 3–5 variants + deterministic claim validation. |
| [`prompts/advertiser_understanding.md`](../prompts/advertiser_understanding.md) | Extract only supported facts. |
| [`prompts/publisher_reasoning.md`](../prompts/publisher_reasoning.md) | Business-relevance copy from already-ranked evidence. |
| [`prompts/missing_information.md`](../prompts/missing_information.md) | One required or useful question, never a value. |
| [`prompts/ad_creative.md`](../prompts/ad_creative.md) | Ad copy for publisher × persona combos. Claims are validated in code. |
| [`docs/RANKING.md`](./RANKING.md) | Scale / hosting notes. |
| [`frontend/src/api.ts`](../frontend/src/api.ts) | SSE reader for `POST /api/run/stream`. |
| [`frontend/src/App.tsx`](../frontend/src/App.tsx) | Chat UI; clarify answer reuses `thread_id`. |

Live prompts are `advertiser_understanding.md`, `publisher_reasoning.md`, `missing_information.md`, and `ad_creative.md`.

Earlier sweep (still gone): leftover `graph/` and `llm/` packages and unused prompts (`clarify.md`, `creative.md`, `extract_brief.md`, `orchestrator.md`, `refine_brief.md`, `why_personas.md`, `why_publishers.md`). Phase 2 dead-code list is under **Phase 2 — What shipped**.

---

## Phase 1 — What shipped (verified 2026-09-04)

Pipeline:

```
query → understand (LLM or heuristic)
     → InMemoryPublisherRetriever
     → deterministic rank (score ≠ confidence)
     → reason (copy only)
```

Entry: `app.agents.iter_run()` for SSE; `app.agents.run()` for tests. Matching is not in `agents.py`.

**Live API** (`backend/app/main.py`): `/`, `/api/health`, `/api/examples`, `POST /api/run/stream` (SSE `stage` / `section` / `token` / `clarify` / `done` / `error`). The Vite chat calls it through the dev proxy. `section.kind` starts a new assistant bubble (`publishers` | `personas` | `ads`).

**Graph nodes** (`graph.py`): `parse_advertiser` → `analyze_missing` → `halt_required` (END) if required product gap, else `ready_to_place` fans out `rank_publishers` ║ `match_personas` → `assemble_result` (`reason_about_matches` + `render_text`) → END if useful shopper question, else `creative_generation` → `validate_creatives`. Creatives are one `parse_structured` batch in `creative.py`, not a `Send` fan-out. Useful shopper resume is `agents.run_ads(snapshot)` — ads only.

**SSE** (`main.py`): loops `iter_run()` with a `finished` flag; after `halt_required` or a useful `clarify`, keeps consuming the iterator so LangGraph closes cleanly and LangSmith does not log `GeneratorExit` traces. Pinned by `test_stream_exhausts_iter_run_after_clarify` and `test_stream_clarify_does_not_emit_error`.

**Understand:** `extract_profile()` uses `parse_structured` + `advertiser_understanding.md` when `llm_enabled()`; otherwise `extract_heuristic()` (phrase table in `understand.py`). `DISCO_FORCE_HEURISTIC=1` disables the model (tests set this in `backend/tests/conftest.py`). Runtime LLM flag: `LLM_API_KEY` or `OPENAI_API_KEY`. `/api/health` reports `llm` from `llm_enabled()`, so it matches the runtime rule (either key, and `DISCO_FORCE_HEURISTIC=1` wins). Heuristic `_PRICE` treats `$` + 2–5 digits as a price (`$40` → budget); years/zips without `$` or thousands-commas stay unknown. LLM extract failure logs a warning and falls back to heuristic.

**Retrieve:** Live path is `retrieve_all()` then a `pool_size` slice only. OpenAI publisher vectors are loaded from `backend/.cache/publisher_embeddings.json` (or `DISCO_EMBED_CACHE`) when the model + text hashes match, otherwise those rows are embedded and the file is rewritten. Advertiser query vectors are not cached. `HashEmbedder` does not use the file.

**Rank:** `score` = weighted evidence (category / subcategory / product / keyword / semantic / audience / economic / behavioral / business-model, then penalties). `confidence` = how complete/trustworthy that evidence is (`match_confidence`). They are separate fields. Top-N = 4. Fallback if nothing is eligible: up to 2 least-wrong rows marked `weak`, never a `category_mismatch` row.

**Reason:** `reason_about_matches()` writes headlines / why / caveats / remainder (LLM when enabled, else `reason_heuristic`). It does not re-rank. `render_text()` is the SSE publisher block. Remainder is one sentence (`I left the rest of the catalog out — N are a different category, N are only a weak or indirect match.`), not a paragraph per leftover publisher. Catalog-gap caveat is shown once; “no advertiser audience was stated” is not used as a caveat.

**Clarify resume:** `main.py` keeps `thread_id → original query` in a bounded in-process dict (`_PENDING_MAX` 256). A reload or a second worker drops it and resume returns **400**; the client starts over.

**Frontend:** `App.tsx` starts a new assistant bubble on each `section` event. Chips render only on the latest `clarify` bubble (Skip only when `allow_skip`). `onDone` closes streaming; it does not replace the staged bubbles with combined `reply.text`. Stage copy: understand / publishers / personas / creatives.

### Run

```bash
cd backend && uvicorn app.main:app --reload --app-dir .
cd frontend && npm run dev   # http://127.0.0.1:5173
```

Set `LLM_API_KEY` (or `OPENAI_API_KEY`) in `backend/.env` for extraction and explanation. Without it, the heuristic still ranks. Do not echo `.env` values.

---

## Phase 2 — What shipped (verified 2026-09-04)

Sits on Phase 1 ranking. Matching is not in `agents.py`.

```
parse_advertiser → analyze_missing
     → halt_required (END)                         # required product question; no rank/ads
     → ready_to_place → rank_publishers ║ match_personas
     → assemble_result (reason_about_matches)
          → END if useful shopper question
          → creative_generation → validate_creatives
```

- **Required unanswered question** is the whole reply (`halt_required`). No ranking, no personas, no ads. Pinned by `test_required_question_does_not_spend_a_creative_batch` and `test_vague_run_is_required_question_only`.
- **Useful shopper question** comes after publishers + personas (`audience_question`). Chips are matched persona names. Skip is allowed. Ads wait for answer/skip (`run_ads`).
- **Skip on required via API** (`skip: true` on a pending required thread): `missing.py` will not re-ask a skipped field, but no product and no category is still `insufficient_signal`, so `chosen` is empty.
- **Personas:** `data.py::load_personas()` reads 10 rows from `backend/app/data/shopper_personas.json`. Scoring is Python-only in `personas.py`. Alcohol/whisky maps to gourmet/premium grocery affinities so chips are Gifter / Affluent Classic, not Pet Parent.
- **Creatives:** one LLM/heuristic batch for up to 4 publisher × persona combos (`creative.py::_llm_variants`). No `langgraph.types.Send`.
- **Dead this loop (gone from the tree):** `GraphState.question`, `done.followup`, `understand.clarification_question()`, `schemas.MissingItem`.

Campaign config is not built.

---

## Decisions / constraints

- Do not force `brief.category` to be one of the 10 publisher `category` strings and hard-gate on it. Name the product; walk the catalog as a shelf, then audience.
- If no real shelf: still return a least-wrong row, mark **weak**, and state the catalog gap in the why. Do not invent fit.
- At most one question per reply. Required (no product and no category) is a `clarify` event with no publishers. Useful shopper questions are a `clarify` after the personas section and carry Skip. Resume requires `thread_id`. Useful resume is ads-only (`run_ads`); required resume is a full `run()`.
- Ranking weights in `ranking.py` are initial heuristics, not learned.
- Production retrieve (metadata filter + BM25 + HNSW → RRF) is documented, not built. Do not add ANN for 20 rows.
- LangGraph is the live orchestrator (`langgraph>=0.6` in `backend/pyproject.toml`). LangChain is not a dependency; the `langsmith` client only arrives transitively via LangGraph, which is why `LANGSMITH_TRACING` + `LANGSMITH_API_KEY` work without an SDK of our own. Do not add the LangSmith SDK.
- Personas: `data.py::load_personas()` reads `shopper_personas.json` (10 rows, `backend/app/data/`; the assignment pack keeps the original). Scoring is Python-only in `personas.py`. Do not start campaign config unless the user asks.
- Creatives are one LLM batch for up to 4 publisher × persona combos (`creative.py`). That batch is deliberate. Do not fan out with `Send`.

---

## Historical: why ranking felt wrong

Query ≈ *“We make Cuddle / adult diapers, best quality money can buy.”*

Old path (removed): orchestrator **must pick one catalog category** → often `wellness_dtc` → category hard-gate → only **Daily Form (`pub_012`)** passes → rank #1 is automatic → why-LLM is told to *justify* the top list → invented a wellness story.

Daily Form is vitamins/supplements. It is not a diaper placement. That reaction was correct. `test_phase1_examples.py::test_cuddle_adult_diapers_is_not_forced_into_daily_form` pins this: never force Daily Form (`pub_012`) / `wellness_dtc` as a #1 **strong** match. Heuristic/test path today **clarifies** (no phrase-table hit → `insufficient_signal`). A later weak catalog-gap row is also allowed by the test.

---

## Known gaps / risks

- LLM reason path (`reason_about_matches` → `render_text`): recommendations are joined by `publisher_id` from the Python-ranked list. Near-miss bullets render `reasoning.near_misses` as returned by the model (`publisher_name` + explanation), so copy can name a model-supplied near miss. Not a Phase 1 blocker; do not over-index.

## Next steps

1. Wait for the user. Do not start the next feature unasked. Campaign config is not built.
2. Do **not** re-implement matching in `agents.py`.
3. Working tree is **uncommitted**. `git-commit-agent` proposes only. Do not commit or push unless the user asks.

---

## Verification (2026-09-04, Phase 2)

- `cd backend && .venv/bin/pytest -q` — **76 passed**.
- Graph topology: parse → missing halt vs `rank_publishers` ║ `match_personas` → `assemble_result` (`reason_about_matches`) → optional ads.
- SSE: `main.py` exhausts `iter_run` after clarify via `finished` flag; `test_app.py` asserts no `GeneratorExit` in SSE body.
- Embed cache: `backend/.cache/publisher_embeddings.json` (override `DISCO_EMBED_CACHE`).
- `test_phase2.py` covers required vs useful, Skip, free-text answers, persona ranking, creative personalization, claim validation, Phase 1 ranking preserved, and that a required question never spends a creative batch.
- `cd frontend && npm run build` — exit 0.
- `parse_structured` omits `temperature` (some models reject `0`).

Use `.venv/bin/pytest`, never bare `pytest`.

---

## Do not

- Tell the next agent to implement matching in `agents.py`.
- Start campaign config unless the user asks.
- Let the LLM rank, invent scores, or pick winners from the full 20-row list.
- Treat “closest catalog category” as the product identity.
- Commit or push unless the user asks (`git-commit-agent` proposes only).
