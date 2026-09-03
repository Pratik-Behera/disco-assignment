# Changelog

Verified record of repository changes. Updated by `change-tracker-agent` from git and code evidence only.

## [Unreleased]

Working tree. The backend was reset to a thin FastAPI shell; the LangChain / LangGraph stack, the engine package, the scorer, and the Jinja screens are all deleted. Verified against the current tree, not against earlier plans.

- Fixed clarify resume silently dropping the advertiser's original query: an unknown or expired `thread_id` now returns 400 instead of running the agent with empty input, and the pending-thread map is bounded at 256 entries (`backend/app/main.py`).
- Fixed `/api/examples` returning the file's `---` rule as an advertiser example and leaking the `1. ` list numbering into the query text (`backend/app/data.py`).
- Stopped streaming raw exception text to the browser on the SSE `error` event; the traceback is logged server-side instead, so a provider auth error cannot carry an API key into the page (`backend/app/main.py`).
- Fixed the SSE token stream collapsing newlines, so a multi-line reply no longer reflows when the final `done` payload lands (`backend/app/main.py`).
- Fixed the frontend leaving a blinking caret under a reply that failed mid-stream, leaving the response body open after an `error` event, and crashing when `/api/examples` returns a non-list (`frontend/src/App.tsx`, `frontend/src/api.ts`).
- Removed the unused `budget_usd` field from the run request and the unused `framer-motion` dependency (`frontend/`).
- Reset the backend to `main.py` (SSE API), `agents.py` (placeholder `run()`), `data.py` (catalog loaders), and 7 API tests; dependencies are now fastapi + uvicorn + pydantic only (`backend/pyproject.toml`).
- Added the Vite + React chat UI: single-column stream, example chips, one-question clarify round (`frontend/src/`).
- Kept versioned prompts under `prompts/` (`extract_brief`, `clarify`, `refine_brief`, `creative`, `orchestrator`, `why_*`); no loader wired yet.
- Added ranking/hosting notes (`docs/RANKING.md`), status (`docs/STATUS.md`), and next-agent handoff (`docs/HANDOFF.md`).

## 2026-09-01 — Take-home candidate pack

- Added Disco take-home materials under `disco-takehome-candidate/` (README, GLOSSARY, `publishers.json`, `shopper_personas.json`, `example_advertisers.txt`).

<!-- last-sync: 2026-09-03, working-tree -->
