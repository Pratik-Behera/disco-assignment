# Disco campaign builder

Advertiser one-liner in, ranked publishers out — with why, near misses, and a grouped “everyone else.”

## Stack

- **Frontend:** Vite + React at http://127.0.0.1:5173
- **Backend:** FastAPI SSE shell (`backend/app/main.py`). Matching lives in `backend/app/agents.py` → LangGraph.

## Phase 1 pipeline

```
query → understand (LLM or conservative heuristic)
     → in-memory retrieve (structured + keyword + cosine)
     → deterministic rank (score ≠ confidence)
     → reason (copy only; does not re-rank)
```

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

Set `LLM_API_KEY` in `backend/.env` for extraction and explanation. Without it, a conservative heuristic still ranks.

Tests: `cd backend && .venv/bin/pytest -q`

## Layout

| Path | Role |
|------|------|
| `backend/app/graph.py` | LangGraph: parse → retrieve → score → reason |
| `backend/app/retrieval.py` | `PublisherRetriever` + in-memory scan |
| `backend/app/ranking.py` | Heuristic weights, penalties, confidence |
| `backend/app/understand.py` / `reason.py` | Language nodes |
| `prompts/advertiser_understanding.md` | Extract only supported facts |
| `prompts/publisher_reasoning.md` | Business-relevance copy |
| `backend/app/main.py` | SSE only |

Ranking weights in `ranking.py` are initial heuristics, not learned. Calibrate when campaign outcomes exist.
