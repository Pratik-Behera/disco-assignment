# Disco campaign builder

Type what you sell. Get back where the ads should run, who they should talk to, the copy, and a starter spend plan.

## What I built, and how to run it

- You describe the business in a sentence. (“We sell premium dog food for senior dogs.”)
- If that sentence is too vague, the app asks **one** follow-up. If it is clear, it just goes.
- It recommends the best **places to advertise** from the catalog, says why they fit, and why the rest did not.
- It names the **kinds of shoppers** those ads should talk to.
- It writes a few **ad variants** (headline + body) for those shoppers — no invented medical miracles.
- It drafts a **campaign**: goal, budget, duration, who we target, how the dollars split across publishers, and a starting bid.

Numbers (scores, ranks, budgets, bids) come from code. The model writes the English. It does not pick winners, and it does not invent the math.

```
read the brief
  → stop if the product is unclear
  → score publishers and shoppers in parallel   (code)
  → write the "why"                             (model)
  → write ads, then check claims                (model, then code)
  → ask for goal / budget / duration if missing
  → split dollars and set bids                  (code)
  → explain the plan                            (model)
```

The campaign shape is the smallest set that could actually launch: objective, total + daily budget, duration, targeting from matched shoppers/publishers, a split of spend across recommended publishers, and a starting bid by goal. Geo, auctions, and frequency caps are left out — we have no data for them, and guessing would be fan fiction.

**Run it**

```bash
docker compose up --build
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

> **Disclaimer.** Copy `backend/.env.example` to `backend/.env`.
>
> Please add an `OPENAI_API_KEY` for the full demo — the publisher reasons, the ad copy, and the campaign write-up. Without it, ranking still works; the writing falls back to templates. You'll still get ads. You just won't want to run them.
>
> Add a `LANGSMITH_API_KEY` and set `LANGSMITH_TRACING=true` if you want traces in LangSmith. Same run, you just get to watch every node fire.

No Docker: backend `uvicorn` on `:8000`, frontend `npm run dev` on `:5173`. Prompts live in `prompts/`.

## If I had another week

I would stop treating 20 JSON rows like a product and stand up a real production setup: a database for campaigns and catalogs, a cache so the same brief is not re-scored for sport, a vector store for retrieval, ranking algorithms we can measure, a deploy someone can hit without cloning the repo, write guardrails so ads stay claim-safe, evals so a prompt tweak cannot silently tank match quality, and log monitoring so “the model said something weird” is a query, not a vibe.

## What I intentionally cut, and why

This question is “what did you refuse to build, on purpose?” A take-home has a time budget. Building everything is how you ship nothing. The interesting answer is the stuff that *looked* impressive and still got cut.

I cut anything that does nothing useful at 20 publishers:

- A vector database, BM25, HNSW, the whole retrieval costume party. Scanning 20 rows in memory is faster than the meeting where we debate ANN indexes.
- A real database, auth, and saved campaigns. Nobody is coming back tomorrow to resume a candle brief.
- Letting the LLM pick the winners. It is a solid copywriter and a terrible accountant. Scores, budgets, and bids stay in Python.
- Image ads, live auctions, geo targeting, multi-region deploy. Fun. Not the assignment. Also not 6–8 hours.
- A learned ranker. We have no outcome data. “ML” without labels is astrology with extra steps.

A half-finished Pinecone cluster is worse than a chat that ranks, explains, writes ads, and allocates spend. I would rather defend a small working system than narrate a large unfinished one.

## Hard vs easy — where the interesting work actually lives

Honest take: with ~20 publishers in a JSON file, this problem is not hard. Matching “dog food” to a pet retailer is a weekend project. Writing prompts and chaining LLM calls is the easy part. It looks like AI engineering. Mostly, it is not.

The trap is overengineering. You can burn the whole time budget on agents, graphs, and “production architecture” for a catalog that fits in a tweet. I got a little dizzy myself — the brief is simple, the data is tiny, and the temptation to build a platform is loud.

What is actually hard shows up when the catalog is 100K publishers and speed matters:

- **Ranking.** Which signals survive at scale, and how you prove one algorithm beats another.
- **Retrieval / context.** Ingesting the *right* slice of a huge catalog into the agent so it has context without drowning. That is the fun part.
- **Guardrails and evals.** The model will happily invent a joint-health claim. Catching that in CI is the job.
- **Serving.** Cache, indexes, and logs so the pipeline is fast — and debuggable when it is not.

The interesting engineering is not “can the LLM write an ad.” It is “can we get the relevant rows in front of the agent, rank them in time, and know when we were wrong.”