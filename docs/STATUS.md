# Status

Canonical next-agent context: **[docs/HANDOFF.md](./HANDOFF.md)**.

Backend is a thin FastAPI shell: `/api/health`, `/api/examples`, and `POST /api/run/stream` (SSE stage/token/clarify/done). The Vite chat UI is wired to it end to end. `app/agents.py:run()` is still a placeholder — matching and ranking are the next work.

Tests: `cd backend && .venv/bin/pytest -q` (7 passed)
