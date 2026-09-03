# Disco campaign builder

Advertiser one-liner in, publisher recommendation out.

## Stack

- **Frontend:** Vite + React at http://127.0.0.1:5173
- **Backend:** Thin FastAPI shell — agent logic goes in `backend/app/agents.py`

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

Tests: `cd backend && .venv/bin/pytest -q`

## Where to build

| File | Purpose |
|------|---------|
| `backend/app/agents.py` | Clarify → filter catalog → reasoner (your main work) |
| `backend/app/data.py` | Loads `publishers.json`, personas, examples |
| `backend/app/main.py` | SSE API only — don't put matching logic here |
| `prompts/` | Prompt files when you add an LLM |

Set `LLM_API_KEY` in `backend/.env` when you're ready to call a model.
