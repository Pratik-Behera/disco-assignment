# Disco campaign builder

Advertiser one-liner in, ranked publishers out — with why, near misses, and a grouped “everyone else.”

## Stack

- **Frontend:** Vite + React at http://127.0.0.1:5173
- **Backend:** FastAPI SSE shell (`backend/app/main.py`). `agents.py` is the entry only — matching lives in `graph.py` plus `understand.py` / `retrieval.py` / `ranking.py` / `reason.py` / `personas.py`.

## Pipeline

```
query → understand
     → rank publishers ║ analyze missing ║ match personas
     → assemble
     → creatives → validate
```

Phase 1 ranking is unchanged and still Python-only. Missing-info and persona scoring run in the same LangGraph superstep as ranking. Creatives use compact publisher context, not the raw catalog row.

Python assigns scores. The model writes language. It never sees the full 20-row list to pick winners.

**Current prototype:** in-memory retrieval + semantic similarity + deterministic ranking.

**Production (not built):** metadata filter + BM25 + HNSW → RRF → candidate rerank → top 5–10 → LLM reasoning.

## Run locally

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --app-dir .
```

```bash
cd frontend
npm install
npm run dev
```

Set `LLM_API_KEY` (or `OPENAI_API_KEY`) in `backend/.env` for extraction and explanation. Without it, a conservative heuristic still ranks; `DISCO_FORCE_HEURISTIC=1` forces that path. `/api/health` reports `llm` from the same rule.

Tests: `cd backend && .venv/bin/pytest -q`

## Layout

| Path | Role |
|------|------|
| `backend/app/agents.py` | Entry only: `run()` → `get_graph().invoke(...)` |
| `backend/app/graph.py` | LangGraph: required missing first, then rank ║ personas, then ads |
| `backend/app/retrieval.py` | `PublisherRetriever` + in-memory scan |
| `backend/app/ranking.py` | Heuristic weights, penalties, confidence |
| `backend/app/understand.py` / `reason.py` | Language nodes |
| `backend/app/missing.py` / `personas.py` / `creative.py` | Questions, persona scores, ad variants |
| `prompts/advertiser_understanding.md` | Extract only supported facts |
| `prompts/publisher_reasoning.md` | Business-relevance copy |
| `prompts/missing_information.md` / `ad_creative.md` | One useful question; claim-safe ads |
| `backend/app/main.py` | SSE only |

Ranking weights in `ranking.py` are initial heuristics, not learned. Calibrate when campaign outcomes exist.
