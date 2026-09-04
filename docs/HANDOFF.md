# Handoff — Disco campaign builder

Use this file as the next-agent brief. It is the current-state map, not a changelog. Short snapshot: [`docs/STATUS.md`](./STATUS.md). Ranking notes: [`docs/RANKING.md`](./RANKING.md). Assignment pack: [`disco-takehome-candidate/README.md`](../disco-takehome-candidate/README.md).

Updated: 2026-09-04 after campaign revision merge fixes + UX. HEAD `148efcd` on `main`. Phase 3 + UX are in the working tree and **not committed**. Phase 2 remains at `148efcd`.

---

## Read this first

Disco is a **retail media network**: publishers are retailers (or retailer-like) that sell ad space. An advertiser types one sentence. The system returns ranked publishers, matched shopper personas, 3–5 ad variants, and a draft campaign plan, plus at most one follow-up question with quick-reply chips.

**Assignment vs live catalog.** Original JSON lives in `disco-takehome-candidate/data/`. Runtime copies are `backend/app/data/` (20 publishers, 10 shopper personas, example advertiser lines). Edit the live copies if ranking behavior must change; keep the pack as the source dump.

**Hard rule.** Python ranks, scores, allocates, and validates. The LLM writes language only. No scores, ranks, bids, or budgets from a model. Matching is **not** in `agents.py`. Campaign arithmetic is **not** in the strategist prompt.

**User working style.** Implement in-chat. Do not spawn orc / explore / review agents unless asked. Do not commit unless asked (`git-commit-agent` proposes only). Do not print `.env` secrets. Campaign config (assignment item 3) is built. Do not start DSP / auction work unless asked.

**Last three commits**

| SHA | Message |
|-----|---------|
| `d501781` | `feat(backend): extend ranking into a staged campaign graph` |
| `f7e0489` | `feat(frontend): reveal publishers, shoppers, and ads as they land` |
| `148efcd` | `docs: record Phase 2 pipeline and current status` |

---

## Intended conversation

This is the product contract. Preserve it unless the user changes it.

1. Vague product (no product and no category) → **required** question, no Skip, **no** publishers / personas / ads.
2. Catalog gap (product clear, **no** ranked publishers) → one honest “no shelf” message. No “here’s where I’d start.” No personas, chips, ads, or campaign questions.
3. Real matches → publishers, then shopper tiles (catalog name + paraphrase + why), then tagline ads in the same turn. Chips stay paraphrases.
4. Audience not already in the query → **useful** shopper question **after ads** (Skip allowed). Chips are paraphrases (`People hunting a better price`), mapped back to `persona_id`. Skip goes to campaign without rewriting ads. A chip pick is `run_ads` then `run_campaign`. No re-rank.
5. Audience already stated (`women`, `senior`, `gifts` / `gifting`, `owners`, etc. — see `_AUDIENCE_MARK` in `missing.py`) → skip that question, go to campaign after ads.
6. After that: required campaign inputs are `campaign_objective`, `total_budget_usd`, `campaign_duration`. Never assume duration from budget. One question at a time. Required = no Skip; useful `performance_goal` (conversions without a CPA/ROAS) = Skip allowed. No geography campaign question. Budget chips are `$100` / `$500` / `$2,000`; the composer accepts a typed amount (`500`, `$200`, `200 dollars`) via `parse_budget_answer`. `$40 candles` is still not a campaign budget.
7. Campaign revisions recalc campaign only via `run_campaign`. `is_campaign_revision` is True only for chip labels (`Build awareness`, `Drive traffic`, `Drive purchases`), budget-only text (`$15k`), and duration-only text (`60 days`). False for product copy even if extract would fire (`candles for 30 days`, `drive traffic to our site`) — that starts a new run. UI keeps `thread_id` after `done`. `run_campaign` seeds from `raw_query`, then `snapshot.campaign_inputs` wins (`CampaignInputs.merge`). Revision SSE sets `answers=[]` and applies `raw_update` last so a later `$15k` is not overwritten by the original sentence or replayed answers.
8. UI is ChatGPT-style markdown (bold, headings) plus labeled sections: **Shoppers I’d write for** (catalog name + paraphrase + why) and **Ad creatives** (catalog-name pill, why, headline, body, CTA). Resume loading copy follows the question just answered (campaign fields → “Drafting the campaign plan…”, shopper chip → ads, skip-after-ads → campaign). Loading dots hide while a bubble is streaming.

---

## Architecture

```
START
 └─ parse_advertiser          # extract_profile + merge answers/clarification
 └─ analyze_missing           # required product gap only
      ├ halt_required → END   # status=insufficient_signal; question only
      └ ready_to_place        # empty fan-out hub (conditional edge returns one name)
           ├ rank_publishers  ║
           └ match_personas   ║   same LangGraph superstep
           └ assemble_result  # reason_about_matches + render_text; no shopper question
                ├ END if no recs (catalog gap)
                └ creative_generation → validate_creatives  # ads, then audience_question
                     ├ END if useful shopper rewrite
                     └ campaign_input_analysis
                          ├ END if campaign question
                          └ build_campaign → campaign_llm_strategist → END
```

Official LangGraph: multiple outgoing edges = one superstep; join waits for both. `ready_to_place` exists because a conditional edge can return only one name.

**Entry (do not collapse these)**

| Function | Where | Role |
|----------|-------|------|
| `run()` | `agents.py` | Sync / tests. `get_graph().invoke(...)`. |
| `iter_run()` | `agents.py` | SSE. `stream_mode="updates"`, yields `(node, AgentResult)`. Catches `GeneratorExit`. |
| `run_ads(snapshot)` | `agents.py` | Useful-clarify resume. Ads only. Does not re-rank. `prefer_matches` uses only the `target_audience` answer. SSE then calls `run_campaign`. |
| `run_campaign(snapshot)` | `agents.py` | Campaign question resume or budget/duration/chip-label edit. Seeds query, snapshot wins, then `raw_update`. Does not re-rank. |
| FastAPI | `main.py` | Health, examples, `POST /api/run/stream`. Does **not** call `run()`. `_pending.phase` is `ads` / `campaign` / `revision`. |

Creatives are **one** `parse_structured` batch in `creative.py` for up to 4 publisher × persona combos. Do not fan out with `langgraph.types.Send`.

---

## SSE contract

**Endpoint:** `POST /api/run/stream`  
**Media type:** `text/event-stream`  
**Client:** `frontend/src/api.ts` → `streamRun()`. Vite proxy to the backend.

| Event | Payload | When |
|-------|---------|------|
| `stage` | `{ "stage": "understand" \| "publishers" \| "personas" \| "creatives" \| "campaign" }` | Loading copy in the UI. Personas stage is emitted from `_section("personas", ...)`. Campaign stage from `_section("campaign", ...)` / `campaign_llm_strategist`. |
| `section` | `{ "kind": "publishers" \| "personas" \| "ads" \| "campaign" }` | Starts a new assistant bubble. |
| `token` | `{ "text": "<word+whitespace>" }` | Word drip (`_token_chunks`, 10ms). |
| `clarify` | `{ thread_id, question, field, importance, quick_replies, allow_free_text, allow_skip, ... }` | Required halt, useful shopper question, or campaign input question. Useful / campaign clarify has **no** `done`. |
| `done` | `{ thread_id, text, chosen, personas, creatives }` | Terminal success. UI closes streaming; it does **not** replace staged bubbles with `text`. `onDone` keeps `thread_id`. |
| `error` | `{ "detail": "..." }` | Caught exception. Never leak API keys. |

**Resume body:** `{ raw_input: "", thread_id, resume?: string, skip?: bool }`  
**Ads resume:** `_pending.phase == "ads"` → ads section, then `run_campaign` (campaign question or plan).  
**Campaign resume:** `phase == "campaign"` → `run_campaign` only.  
**Revision:** `phase == "revision"` + `thread_id` + new `raw_input` that `is_campaign_revision` accepts → `answers=[]`, `raw_update=body.raw_input`, `run_campaign` only. Other text (including product copy) starts a new run.

**Must drain the iterator.** `main.py` sets `finished=True` after clarify/done then **keeps consuming** `iter_run()` (`if finished: continue`). Returning early abandons `graph.stream()` and LangSmith logs a `GeneratorExit` trace. Client tab-close mid-run can still show that. Pinned by `test_stream_exhausts_iter_run_after_clarify` and `test_iter_run_exhausts_after_campaign_clarify`.

**In-process `_pending`.** `thread_id → { query, field, asked, skipped, snapshot, phase }` , max 256. `phase` is `ads` / `campaign` / `revision`. `uvicorn --reload` or process restart drops the map; resume is **400** (`Unknown or expired thread_id`). Not a new store — client starts over.

---

## File map

| Path | Role |
|------|------|
| [`backend/app/agents.py`](../backend/app/agents.py) | Entry only: `run` / `iter_run` / `run_ads` / `run_campaign`. No matching. |
| [`backend/app/graph.py`](../backend/app/graph.py) | LangGraph topology + `GraphState`. |
| [`backend/app/main.py`](../backend/app/main.py) | FastAPI SSE. Exhausts `iter_run`. `_pending.phase` ads/campaign/revision. |
| [`backend/app/understand.py`](../backend/app/understand.py) | Advertiser extract: LLM structured parse or conservative heuristic phrase table. |
| [`backend/app/retrieval.py`](../backend/app/retrieval.py) | `retrieve_all()` + `pool_size` slice. OpenAI publisher embed disk cache. Protocol has no `retrieve()`. |
| [`backend/app/ranking.py`](../backend/app/ranking.py) | Deterministic score, confidence, eligibility, top-N, near misses. |
| [`backend/app/reason.py`](../backend/app/reason.py) | Publisher copy only. `reason_about_matches` (LLM or `reason_heuristic`) + `render_text`. Does not re-rank. |
| [`backend/app/missing.py`](../backend/app/missing.py) | Required product question; `audience_question` after ads. |
| [`backend/app/personas.py`](../backend/app/personas.py) | Python scores all 10 personas. Unknown is not mismatch. |
| [`backend/app/creative.py`](../backend/app/creative.py) | One-batch ads + deterministic claim/length validation. |
| [`backend/app/campaign.py`](../backend/app/campaign.py) | Extract inputs, targeting, allocation, `recommend_bid`, validate, render. LLM explains only. |
| [`backend/app/llm.py`](../backend/app/llm.py) | Prompt loader, `llm_enabled()`, OpenAI chat parse, `embed_texts()`. Ranking never goes through here. |
| [`backend/app/schemas.py`](../backend/app/schemas.py) | Shared types. `CampaignInputs` has no geography. `FieldSource` is `advertiser` \| `persona` \| `publisher`. |
| [`backend/app/data.py`](../backend/app/data.py) | Loads publishers (20), personas (10), examples. |
| [`backend/app/data/publishers.json`](../backend/app/data/publishers.json) | Live publisher catalog. |
| [`backend/app/data/shopper_personas.json`](../backend/app/data/shopper_personas.json) | Live persona catalog. |
| [`prompts/advertiser_understanding.md`](../prompts/advertiser_understanding.md) | Extract only supported facts. |
| [`prompts/publisher_reasoning.md`](../prompts/publisher_reasoning.md) | Headlines / why / caveats from already-ranked evidence. |
| [`prompts/missing_information.md`](../prompts/missing_information.md) | Polish the required question. Never invent a value. |
| [`prompts/ad_creative.md`](../prompts/ad_creative.md) | Ad copy for publisher × persona. Claims validated in code. |
| [`prompts/campaign_strategist.md`](../prompts/campaign_strategist.md) | Explain an already-validated CampaignConfig. Do not recalculate. |
| [`frontend/src/App.tsx`](../frontend/src/App.tsx) | Chat UI. Markdown + ad blocks. Dots hide while streaming. Chips + Skip on latest clarify bubble. `onDone` keeps `threadId`. |
| [`frontend/src/api.ts`](../frontend/src/api.ts) | SSE parser. `reader.cancel()` in `finally`. |
| [`frontend/src/types.ts`](../frontend/src/types.ts) | `QuestionMeta`, `ChatReply`. |
| [`backend/tests/conftest.py`](../backend/tests/conftest.py) | Forces `DISCO_FORCE_HEURISTIC=1` and `LANGSMITH_TRACING=false`. |
| [`backend/tests/test_phase2.py`](../backend/tests/test_phase2.py) | Graph, missing/clarify, personas, creatives. |
| [`backend/tests/test_phase3.py`](../backend/tests/test_phase3.py) | Campaign extract, questions, allocation, revisions, `is_campaign_revision`. |
| [`backend/tests/test_app.py`](../backend/tests/test_app.py) | SSE contract, drain, clarify/resume/skip, campaign revision stream. |

Live prompts are those five files only. Earlier unused prompts (`clarify.md`, `creative.md`, `extract_brief.md`, `orchestrator.md`, `refine_brief.md`, `why_personas.md`, `why_publishers.md`) and leftover `graph/` / `llm/` packages are **gone**. Do not resurrect them.

---

## Module notes (current behavior)

**Understand.** `extract_profile()` uses `parse_structured` + `advertiser_understanding.md` when `llm_enabled()`; else `extract_heuristic()`. LLM failure logs a warning and falls back. Heuristic `_PRICE` treats `$` + 2–5 digits as a price (`$40` → budget); years/zips without `$` or thousands-commas stay unknown. `$10,000 over 30 days` is not a product price (`understand._price_position` skips campaign-money).

**Retrieve.** `retrieve_all()` then a `pool_size` slice. OpenAI publisher vectors: `backend/.cache/publisher_embeddings.json` (override `DISCO_EMBED_CACHE`). Keyed by embedding model + per-publisher text SHA256. Partial miss re-embeds only changed rows. Query vectors are **not** cached. `HashEmbedder` never writes the file. First live run after a cache miss is slow (~8s for 20 rows); reload after that should be cheap. Gitignored.

**Rank.** `score` = weighted evidence then penalties. `confidence` = `match_confidence` (completeness), a **separate** field. Top-N = 4. If nothing eligible: up to 2 least-wrong rows marked `weak`, never a `category_mismatch` row. Weights in `ranking.py` are initial heuristics, not learned. Production retrieve (metadata filter + BM25 + HNSW → RRF) is documented in RANKING.md, **not** built. Do not add ANN for 20 rows.

**Reason.** `reason_about_matches()` writes headlines / why / caveats / remainder. Does not re-rank. `render_text()` is the SSE publisher block. Remainder is one sentence (`I left the rest of the catalog out — N are a different category, N are only a weak or indirect match.`). Catalog-gap caveat once. Do not use “no advertiser audience was stated” as a caveat. Heuristic near-miss copy: “close on the {category} shelf, but not a stronger fit…”.

**Missing.** `analyze_missing` = required **product** gap only. `audience_question` runs **after ads** in `validate_creatives`. Chips use `PERSONA_SPEAK` paraphrases, not catalog names. `_AUDIENCE_MARK` includes `gifts?|gifting` so “mostly bought as gifts” skips the shopper question. Skip on required via API: `missing.py` will not re-ask a skipped field, but no product and no category is still `insufficient_signal` → empty `chosen`, no ads. Empty publisher recs also skip personas, ads, and the shopper question.

**Personas.** `PERSONA_WEIGHTS` in `personas.py`. `HashEmbedder` for the semantic leg (not OpenAI). Alcohol/whisky/spirits bridged via `_RELATED_AFFINITY` to gourmet/premium grocery so chips are gift / lasting-quality paraphrases, not Pet Parent. Gift/`gifts`/`gifting` boosts The Gifter. Filter keeps rows with `"category overlap"` or shopping-for-gifts signal, else falls back to top scored. `render_personas` emits `catalog name\\nparaphrase\\nwhy`. Unknown `price_position` skips the price penalty (pinned by tests).

**Creatives.** One LLM or heuristic batch of taglines (`render_ads` is catalog name / why / headline / body / cta). `validate_creatives` is the only validation gate (claims, length). Ads run before the useful shopper question. Required unanswered question must never spend a creative batch (`test_required_question_does_not_spend_a_creative_batch`).

**Campaign.** `extract_campaign_inputs` stays strict on prose (`$40 candles` is not a campaign budget). Budget-only blobs (`$500`, `make it 500`) use `parse_budget_answer`. Dedicated budget answers also use `parse_budget_answer` (`500`, `$200`, `200 dollars`, `2k`). Duration (`about a month` → 30, `two weeks` → 14), objective, optional CPA/ROAS. Duration is never inferred from budget. `run_campaign` seeds `extract_campaign_inputs(raw_query)`, then `snapshot.campaign_inputs` wins, then `apply_campaign_answers`, then `raw_update` last. Allocation = match × audience_fit × log-scaled reach, then water-fill clamp `[0.08, 0.55]` for `n >= 2` (`ALLOC_MIN_PCT` / `ALLOC_MAX_PCT`); `n == 1` is 100%. Percentages sum to 100; dollars reconcile to total. Weak-only recs (`match_strength` not strong/moderate) → empty `publishers[]`, no fabricated split. Bid from `recommend_bid` / `BID_HEURISTICS` (`basis: heuristic`). `validate_campaign_config` fixes daily budget, bid type, and rounding before the strategist. `is_campaign_revision` is chip labels or leftover-empty after stripping budget/duration glue — not product sentences. `CampaignInputs` has no `geography`; `analyze_campaign_missing` never asks geography. Targeting `FieldSource` is `advertiser` | `persona` | `publisher` only. Graph splits `build_campaign` (Python) vs `campaign_llm_strategist` (explain via `campaign_strategist.md`).

**Frontend.** New `section` → new assistant message. Personas and ads have labeled headings. Resume `stage` follows the last question field (`stageForResume`). Chips only on the latest `clarify` bubble. Skip only when `allow_skip`. Budget question placeholder is “Or type an amount, e.g. 200”. Loading dots show only while `busy` and nothing is streaming. `onDone` keeps `threadId` so a later `$15k` / `60 days` / chip label is a campaign revision.

---

## Env, run, test

```bash
cd backend && uvicorn app.main:app --reload --app-dir .
cd frontend && npm run dev   # http://127.0.0.1:5173
```

| Variable | Effect |
|----------|--------|
| `LLM_API_KEY` or `OPENAI_API_KEY` | Enables extract, missing polish, publisher reason, creatives, campaign explanation, OpenAI embeds. |
| `DISCO_FORCE_HEURISTIC=1` | Disables all LLM paths. Tests set this in `conftest.py`. |
| `OPENAI_MODEL` | Default `gpt-4o-mini`. |
| `OPENAI_EMBEDDING_MODEL` | Default `text-embedding-3-small`. |
| `DISCO_EMBED_CACHE` | Override embed cache path. |
| `LANGSMITH_TRACING` + `LANGSMITH_API_KEY` | Traces via LangGraph’s transitive `langsmith` client. Do **not** add the LangSmith SDK. Tests force tracing off. |

`/api/health` reports `llm` from `llm_enabled()` (either key; `DISCO_FORCE_HEURISTIC=1` wins) and `langsmith` from tracing+key. Do not echo `.env` values. `parse_structured` omits `temperature` (some models reject `0`).

**CI (no `scripts/ci-local.sh`, no GitHub Actions):**

```bash
cd backend && .venv/bin/pytest -q    # 115 passed, 1 skipped as of 2026-09-04
cd frontend && npm run build         # exit 0
```

Always `.venv/bin/pytest`, never bare `pytest`. Integration tests exercise the **heuristic** path. There is no pytest coverage of live LLM reason/creative/missing/campaign-strategist unless someone unsets `DISCO_FORCE_HEURISTIC`. The skip is `test_catalog_all_weak_run_has_empty_publishers` when the catalog is not all-weak for that query; empty allocation is still pinned by `test_no_strong_match_does_not_fabricate_allocation`.

---

## Decisions (do not reverse without asking)

- Do not force `brief.category` to one of the 10 publisher `category` strings and hard-gate on it. Name the product; walk the catalog as a shelf, then audience.
- If no real shelf: still return a least-wrong row, mark **weak**, and state the catalog gap. Do not invent fit.
- At most one question per reply. Required = `clarify` with no publishers. Catalog gap = `done` with no personas. Useful shopper rewrite = `clarify` after ads, Skip allowed. Resume needs `thread_id`.
- LangGraph is the live orchestrator (`langgraph>=0.6`). LangChain is not a dependency.
- Personas scoring stays Python-only.
- Creatives stay one batch. Do not `Send`.
- Campaign config is built. Python allocates and validates; the LLM explains only. Do not add DSP auctions, external ad platforms, or a second campaign agent.
- Never assume campaign duration from budget.
- `is_campaign_revision` stays chip / budget-only / duration-only. Product copy after `done` starts a new run.
- `CampaignInputs` stays objective / budget / duration / optional performance goal. Do not add geography as a campaign question. `FieldSource` stays `advertiser` | `persona` | `publisher`.

---

## Keep vs gone

**Keep (not dead — next agent will be confused if these disappear)**

| Symbol | Why it still exists |
|--------|---------------------|
| `agents.run()` | Tests and sync. SSE uses `iter_run`. |
| `reason_heuristic` | Fallback when LLM is off. |
| `reset_graph()` in `graph.py` | Unused callers; optional test hook. |
| `ranking.insufficient_signal()` | Tests only. Live gate is `missing.py` + `_is_required`. |
| `graph.catalog()` | Used from ranking tests. |
| `agents.run_campaign()` | Campaign resume and revisions. SSE uses it after ads. |
| `campaign.finalize_campaign()` | Tests and `run_campaign`. Graph splits build vs strategist. |
| `campaign.recommend_bid()` | Heuristic bid from objective. `build_campaign_config` calls it. |

**Gone (do not resurrect)**

`understand.clarification_question()`, `schemas.MissingItem`, `GraphState.candidates` / `rejected` / `question`, `done.followup`, `CampaignInputs.geography`, unused prompt files listed above.

---

## Historical: why ranking felt wrong

Query ≈ *“We make Cuddle / adult diapers, best quality money can buy.”*

Old path (removed): orchestrator **must pick one catalog category** → often `wellness_dtc` → category hard-gate → only Daily Form (`pub_012`) passes → rank #1 is automatic → why-LLM is told to *justify* the top list → invented a wellness story.

Daily Form is vitamins/supplements, not a diaper placement. `test_phase1_examples.py::test_cuddle_adult_diapers_is_not_forced_into_daily_form` pins this: never force Daily Form / `wellness_dtc` as a #1 **strong** match. Heuristic/test path today **clarifies** (no phrase-table hit → `insufficient_signal`). A later weak catalog-gap row is also allowed by the test.

---

## Known gaps / risks

- LLM reason path: recommendations are joined by `publisher_id` from the Python-ranked list. Near-miss bullets render `reasoning.near_misses` as returned by the model, so copy can name a model-supplied near miss.
- Embed cache: no file lock; concurrent writes use tmp+replace. Stale after publisher JSON edits until hashes miss. Model switch invalidates all.
- `_pending` is process-local. `uvicorn --reload` and process restart expire `thread_id` (400). Not a new store.
- Campaign revision is gated by `is_campaign_revision` on the follow-up text. A new product sentence starts over.
- Live LLM paths are untested in pytest (`DISCO_FORCE_HEURISTIC=1`).
- `_RELATED_AFFINITY` is brittle for new categories.
- Latency on a cold live LLM trace was ~28s (parse ~6.6s, rank embeds ~8.8s, assemble reason ~3.5s, creatives ~5.1s, plus 10ms/word SSE). Warm embed cache drops the 8s embed.
- `test_catalog_all_weak_run_has_empty_publishers` skips when the live catalog returns a non-weak row for that query.

---

## Next

Wait for the user. Assignment items 1–3 are implemented. Remaining work is polish, README for submission, or hosting — only if asked. Do not start DSP/auction. Do not commit unless asked.

If you change graph, SSE, missing, personas, creatives, or campaign: run `.venv/bin/pytest -q` and `npm run build`. If you change UI behavior, click the chat end-to-end (vague product, clear product, skip useful question, audience already in query, campaign questions, `$15k` revision).

---

## Do not

- Implement matching in `agents.py`.
- Let the LLM rank, invent scores, pick winners, or allocate budget.
- Treat “closest catalog category” as the product identity.
- Abandon `iter_run()` / `graph.stream()` before it ends (LangSmith `GeneratorExit`).
- Fan out creatives with `Send`.
- Add the LangSmith SDK or LangChain.
- Add ANN / production retrieval for 20 in-memory rows.
- Start DSP / auction simulation.
- Commit or push unless the user asks.
- Print `.env` secrets.
- Stage `backend/.env`, `frontend/tsconfig.tsbuildinfo`, or `backend/.cache/`.
