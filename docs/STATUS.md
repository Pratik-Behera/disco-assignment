# Status

Canonical next-agent context: **[docs/HANDOFF.md](./HANDOFF.md)**.

Updated: 2026-09-04.

Phase 1 publisher recommendation is shipped. Entry is `app.agents.run()` → LangGraph in `graph.py` → existing SSE in `main.py`. Do not implement matching in `agents.py`.

Pipeline: query → understand (LLM or heuristic) → `retrieve_all()` + `pool_size` slice → deterministic rank (`score` ≠ `confidence`) → reason (copy only).

Tests: `cd backend && .venv/bin/pytest -q` — **45 passed** (includes `test_reason.py`). Chat reply is compact bullets from `render_text()`; `App.tsx` renders each line (not `whitespace-pre-wrap`). Frontend `npm run build` exit 0.

Personas (`shopper_personas.json`) stay on disk. Do not start them unless the user asks. Do not commit unless they ask. Do not spawn orc unless asked.
