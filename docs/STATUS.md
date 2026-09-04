# Status

Canonical next-agent context: **[docs/HANDOFF.md](./HANDOFF.md)**.

Updated: 2026-09-04. HEAD `148efcd` on `main`. Phase 3 + UX uncommitted (revision merge, labeled tiles).

SSE entry is `app.agents.iter_run()`; tests use `run()`. Ads resume uses `run_ads` then `run_campaign` (`prefer_matches` uses only `target_audience`). Matching is not in `agents.py`. Python allocates/validates; LLM explains only.

Pipeline: parse → missing halt vs parallel `rank_publishers` ║ `match_personas` → assemble (gap may end) → ads → optional shopper rewrite → campaign inputs → build → strategist.

Revision: `run_campaign` seeds from query, snapshot wins, `answers=[]`, `raw_update` last. Budget-only extract via `parse_budget_answer`. `_pending` is in-memory; `--reload` / process restart → 400.

Tests: `cd backend && .venv/bin/pytest -q` — **115 passed, 1 skipped**. Frontend `npm run build` exit 0. UI: labeled shopper/ad sections, catalog names on tiles, `stageForResume`.

Next: wait for the user. Do not commit unless asked. Do not start DSP/auction.
