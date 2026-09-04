# Status

Canonical next-agent context: **[docs/HANDOFF.md](./HANDOFF.md)**.

Updated: 2026-09-04.

Phase 2 sits on Phase 1 ranking. SSE entry is `app.agents.iter_run()`; tests use `run()`. Matching is not in `agents.py`.

Pipeline: parse → missing halt vs parallel `rank_publishers` ║ `match_personas` → assemble (`reason_about_matches`) → optional ads. SSE reveals publishers, then personas, then chips or ads.

Required unanswered question skips rank, copy, and ads. Skip on required via API yields no recs if there is still no product and no category.

SSE exhausts `iter_run` with a `finished` flag after clarify/done so LangSmith does not log `GeneratorExit` traces.

Embed cache: `backend/.cache/publisher_embeddings.json` (override `DISCO_EMBED_CACHE`).

Dead this loop (gone): `GraphState.question`, `done.followup`, `clarification_question`, `MissingItem`.

Personas loaded (`data.py::load_personas()`, 10 rows). Campaign config is not built.

Tests: `cd backend && .venv/bin/pytest -q` — **76 passed**. Frontend `npm run build` exit 0.

User style: implement in-chat. Do not commit unless asked. Do not print `.env` secrets.

Next: wait for the user. Working tree is uncommitted. `git-commit-agent` proposes only.
