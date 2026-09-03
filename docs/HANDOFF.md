# Handoff — Disco campaign builder

For the next agent. Read this before changing ranking or adding an LLM. Do not re-introduce LangChain / LangGraph / LangSmith unless the user explicitly asks.

Repo: `/Users/pratik_behera/PROJECTS/disco-assignment`  
Assignment pack: `disco-takehome-candidate/` (README + glossary + original `data/`). Live catalog copies live in `backend/app/data/`.  
Date of this note: 2026-09-03.

---

## What this is

Advertiser types one sentence. System should return ranked publishers from a 20-row catalog, with why / named exclude. Later: personas, ads, campaign config. Disco is a **retail media network**: publishers are retailers (or retailer-like) that sell ad space on their properties.

**Hard rule:** Python ranks and scores. The model writes language only. No scores, ranks, bids, or budgets from an LLM.

**User working style:** implement in-chat. Do not spawn orc / explore / review agent circus unless asked. Do not commit unless asked. Do not print `.env` secrets.

---

## Current code (verified 2026-09-03)

The backend was reset to a thin shell. There is **no** LangChain / LangGraph, no engine package, no scorer, no LLM client — do not trust older notes that say otherwise.

**Live API** (`backend/app/main.py`): `/`, `/api/health`, `/api/examples`, `POST /api/run/stream` (SSE `stage` / `token` / `clarify` / `done` / `error`). The Vite chat calls it through the dev proxy and works end to end.

**The whole backend is five files:**

| Path | Role |
|---|---|
| `backend/app/main.py` | SSE API only. No matching logic here. |
| `backend/app/agents.py` | `run()` — **placeholder**, returns a stub reply. This is the work. |
| `backend/app/data.py` | Loads publishers (20), personas (10), examples (15) |
| `backend/app/data/` | Catalog JSON, identical to the assignment pack |
| `backend/tests/test_app.py` | 7 API tests, no LLM |

**Clarify resume:** `main.py` keeps `thread_id → original query` in a bounded in-process dict. A reload or a second worker drops it and resume returns **400**; the client starts over. Move it to Redis only if multi-worker becomes real.

**Kept, unused:** `prompts/*.md` (no loader yet). Assignment files under `disco-takehome-candidate/`.

**`backend/.env`:** leftover keys from the old stack; only `LLM_API_KEY` is read, and only to flip a boolean in `/api/health`. Do not echo them. Do not treat them as required.

Tests: `cd backend && .venv/bin/pytest -q` (use `.venv/bin/pytest`, never bare `pytest`). Current: **7 passed**. Frontend: `cd frontend && npm run build`.

### Run

```bash
cd backend && uvicorn app.main:app --reload --app-dir .
cd frontend && npm run dev   # http://127.0.0.1:5173
```

---

## Why ranking felt wrong (the incident)

Query ≈ *“We make Cuddle / adult diapers, best quality money can buy.”*

Old path: orchestrator **must pick one catalog category** → often `wellness_dtc` → category hard-gate → only **Daily Form (`pub_012`)** passes → rank #1 is automatic → why-LLM is told to *justify* the top list → invented a wellness story.

Daily Form is vitamins/supplements, women 22–42, “skeptical of unsubstantiated health claims.” It is **not** a diaper placement.

The user could not relate. That reaction was correct.

---

## Agreed ranking reasoning (use this)

Do **not** force `brief.category` to be one of the 10 publisher `category` strings and hard-gate on it.

Walk the catalog as a **shelf**, then audience:

1. **Name the product**, not a label. Adult diapers = household consumable, repeat, need. Buyer is often 50+ or a caregiver.
2. **Where can the SKU sit?** `category` + `subcategories` + notes. In this JSON, only **Swiftcart (`pub_001`)** has `household` (plus groceries, convenience, instant delivery). That is the only honest #1.
3. **Audience is second.** Marlowe / Linden Park (45–70, quality) are closer *people* for incontinence; they sell workwear, so they are not #1.
4. **Grocery ≠ household.** Pantrygood is organic pantry. Kitchenly is meal kits. Food, not diapers.
5. **If no real shelf:** still return a least-wrong row, but mark **weak match** and say the catalog gap in the why. Do not invent fit.

**This query’s answer:** Swiftcart. No honest #2. Footer: other publishers are other categories. Age caveat: Swiftcart skews 18–34; that is a catalog gap, not a dream fit.

---

## Next work the user already approved

Resolve category ambiguity with **Python first**:

1. Parse the query as a product (job/shelf), not as a required catalog category.
2. Small static **product → shelves** table (not an LLM). Example: `diaper / incontinence / pads` → `{household, pharmacy, grocery}`; `dog food` → `{pet}`.
3. Rank by shelf overlap, then BM25/dense. If zero shelf hits → rank everyone, set `weak_match`.
4. Why-copy **must** state the gap (“no pharmacy/baby retailer; Swiftcart is the only household checkout”). Forbidden: dressing up Daily Form.
5. Clarify **only** if the answer would change the list (adult vs baby). “Are they good quality?” does not.

Language layer (when reintroduced): write the sentence in step 4. Do not pick the list.

Do not add ANN. **ANN = approximate nearest neighbor** (HNSW / FAISS / pgvector), not “artificial neural network.” Useful at ~10k–100k rows. This catalog is 20; brute-force cosine is enough.

---

## Scale / retail-media notes (user asked; do not build)

At 100k: **filter → hybrid retrieve top 50–200 → rerank 20–50 → LLM why on top 3–5.** Never embed-scan 100k. Tags (`kind`, `sells`, `adjacencies`, `exclusions`) find “the 100 that deal with this,” not cosine on notes.

Three publisher kinds — do not mix them:

| Kind | Diaper advertiser? |
|---|---|
| **Retailer that already sells diapers** (Swiftcart, drugstore) | Usually **yes**. Retail media: ad in the aisle; publisher still gets basket + fee. |
| **Brand-owned diaper site** (competitor storefront) | **Never.** Competitive exclusion. |
| **Complementary** (pharmacy / meds / caregiver) | Often a strong #2. Adjacency list, not vibes: incontinence → pharmacy, household, grocery — not `wellness_dtc`. |

---

## Files that matter

| Path | Role |
|---|---|
| `backend/app/agents.py` | Where the shelf table + soft gate + honest why go |
| `backend/app/data/publishers.json` | Live 20 publishers |
| `backend/app/main.py` | Thin FastAPI, SSE only |
| `frontend/src/api.ts` | SSE reader for `POST /api/run/stream` |
| `frontend/src/App.tsx` | ChatGPT-style single column; clarify answer reuses `thread_id` |
| `prompts/` | Old extract/why/creative prompts; no loader |
| `docs/RANKING.md` | Scale / hosting notes (LangGraph and engine mentions are historical) |

---

## Do not

- Re-add LangChain/LangGraph to “get chat working” without the user saying so.
- Let the LLM rank or invent scores.
- Treat “closest catalog category” as the product identity.
- Commit or push unless the user asks.

When they say go: shelf table + soft gate + honest why, then a non-LangChain language path if they specify one.
