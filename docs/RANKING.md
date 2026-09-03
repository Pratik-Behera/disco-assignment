# Ranking methods, scale, and hosting

What we use to rank publishers and personas, when each method is worth the cost, and where this demo can live for free.

This is the matching story for Disco’s take-home: an advertiser types one or two sentences; we return a ranked publisher list (with exclusions) and 3–5 persona-tuned ads. Disco’s live network is the inverse problem (160M+ shopper profiles, 800+ publishers) — same retrieve-then-rank idea, much larger data.

---

## The stack in one line

**Filter (rules) → retrieve (cheap similarity) → optional rerank (local, top 20) → LLM writes reasons and ads only.**

The LLM does not assign scores or bids. At 20–100 catalog rows we can score everyone; at 100k publishers we retrieve first.

---

## Methods

### 1. Structured filters (rules)

Hard gates from catalog fields we already have: category, subcategory overlap, price vs AOV, `disinterested_in`.

- **Cost:** free, microseconds.
- **Scale:** any. This is what you keep even at 100k — you never embed a luxury-handbag brief into every grocery publisher if category already excludes it.
- **Use now:** yes. This is the most honest “why included / why excluded” for a reviewer.

### 2. BM25 (keyword / sparse)

Lexical match on name, category, notes, affinities, description.

- **Cost:** free. No GPU. Tiny RAM.
- **Scale:** fine to millions of short documents.
- **Why:** “grain-free senior dog food” hits `pet` / `pet_food` even when the embedding is vague.
- **Use now:** yes, as half of hybrid retrieve.

### 3. Bi-encoder + cosine (dense)

Embed the query once and each catalog row once. Rank by cosine similarity.

- **Cost:** embed the catalog **once** (at build or first boot). Query time is one embed + a dot product per row.
- **Scale:**
  - **≤ ~10k rows:** brute-force cosine in numpy. No vector database.
  - **~10k–1M:** an ANN index (HNSW) inside Chroma / sqlite-vec / FAISS.
  - **millions+:** a dedicated store (Qdrant, pgvector, Pinecone). Not this assignment.
- **Use now:** yes for a 100-row test set. Precompute vectors and commit them, or embed at startup. Prefer **OpenAI `text-embedding-3-small`** (or equivalent) on the host so we do not load a local transformer in 512 MB RAM. Local `all-MiniLM-L6-v2` is fine on a laptop.

### 4. Reciprocal Rank Fusion (RRF)

Merge the BM25 list and the cosine list without training. Each item’s fused score is `1 / (k + rank)` from each list.

- **Cost:** free.
- **Scale:** whatever the two lists are (we fuse top 50, not 100k).
- **Use now:** yes. This is the hybrid retrieve step.

### 5. Cross-encoder rerank

A second model reads *(query + one publisher)* together and outputs a relevance score. Industry default after bi-encoder retrieve ([bi-encoder vs cross-encoder](https://zeroentropy.dev/articles/biencoder-vs-crossencoder/)). Typical local models: MiniLM / BGE-reranker.

- **Cost:** one forward pass **per pair**. ~20–50 ms per pair on CPU for a small model. **Only run on the shortlist** (20–50), never on the full catalog.
- **Scale:**
  - 20 pairs: fine on a laptop; tight on a 512 MB free host.
  - 100 pairs: still OK locally; skip on free PaaS.
  - 100k pairs: never. Retrieve first.
- **Use now:** **optional, local/dev only.** Skip on Vercel/Render free. Quality gain is real; deploy cost is not worth it for 20 publishers.

### 6. LLM listwise rerank (“put these 10 in order”)

- **Cost:** one (slow) API call. Highest $ and latency.
- **Scale:** at most ~10 candidates.
- **Use now:** **no, for ranking.** Use the same call budget to write *explanations* and *ad copy* for the top 3–5. Ranking stays in Python.

---

## What to run at each catalog size

| Catalog size | Retrieve | Rerank | LLM | Store |
|---|---|---|---|---|
| **20 (this take-home)** | Filter + BM25 + cosine over **all** rows | Skip, or rules only | Why + 3–5 ads | JSON in the repo. No vector DB. |
| **~100 (synthetic test set)** | Same, still brute-force | Optional local cross-encoder on top 20 | Same | Chroma folder **or** a `.npy` / JSON of vectors in git |
| **~10k–100k publishers** | Filter → hybrid retrieve top 50–200 | Cross-encoder on top 20–50 | Why + ads for top 5 | Chroma / sqlite-vec / FAISS |
| **Disco-scale (800+ pubs, 160M shoppers)** | Identity + category filters, then ANN | Domain ranker / auction (their problem, not ours) | Copy only | Real infra. Mention in the README, do not build. |

For the demo we implement the **20-row path** and keep the **100-row path** easy (same code, more JSON). The 100k path is a paragraph in the submission README, not a second product.

---

## Vector store for ~100 test pairs

We may generate extra publishers, personas, and queries to test ranking. That is still tiny.

| Option | Use? |
|---|---|
| **numpy / JSON vectors in the repo** | Best default. Zero service. Survives any host. |
| **Chroma (embedded)** | Fine locally. Painful on free hosts: no persistent disk, extra RAM, extra dependency. |
| **sqlite-vec** | One file, exact search. Good if we already want SQLite. |
| **Qdrant / Pinecone / Weaviate** | No. Servers and bills for a problem of 100 rows. |

Three collections if we use a store: `publishers`, `personas`, `eval_queries`. Metadata (impressions, AOV, dislikes) stays on the JSON objects; vectors are only for retrieve.

---

## Hosting (free, this demo)

The ranking stack above only deploys cheaply if we **do not** load MiniLM + Chroma + a cross-encoder in the web process.

**Do this on the host**

- Catalog JSON + precomputed embeddings (or OpenAI embeddings at query time).
- BM25 + cosine + rules in process.
- OpenAI (or similar) for brief extraction, optional clarify, and ad copy.
- One Python web app (FastAPI or Streamlit).

**Do not do this on a free host**

- Local embedding model + local reranker (80–400 MB weights, 512 MB RAM).
- Chroma as a long-lived disk index (Render free has **no persistent disk**; Vercel functions have **no disk**).
- LangGraph interrupt + in-memory checkpointer across two serverless invocations (state dies).

### Where to put it

| Host | Fits? | Notes |
|---|---|---|
| **Render (free web service)** | **Best single-box Python demo** | FastAPI or Streamlit. 512 MB RAM, 0.1 CPU. Sleeps after 15 min idle; first click can take 30–60 s. 750 free instance-hours/month. No persistent disk. |
| **Streamlit Community Cloud** | Best if the UI is Streamlit | Free, GitHub deploy, Python-native. Same rule: no fat local models. |
| **Vercel** | **Frontend only** | Excellent for Next/React. Python there is serverless: short timeout, no process, no Chroma, bad for LangGraph. Do not put the ranking API here. |
| **Vercel UI + Render API** | Fine if we want a polished React UI | Two free tiers, two URLs, CORS, two deploys. Worth it only if the UI is actually Next.js. |
| **Hugging Face Spaces** | Fine for an ML-flavoured demo | More RAM than Render free if we ever load MiniLM. Overkill for this. |

**Recommendation:** one **Render** web service (or Streamlit Cloud) for the whole prototype. Keep Vercel in reserve only if we add a separate React frontend later. Tell reviewers “first load may be slow; the free instance sleeps.”

OpenAI spend is the only real cost (extraction + 3–5 creatives per run). Embeddings for 100 rows are pennies.

---

## Clarification (not ranking, but it sits in front)

Do not always ask two questions. If the one-liner is clear, skip. If vague, at most two multiple-choice:

1. Category / what they sell.
2. Price band **or** who it is for — not both, and not a full demographic form.

Then show ranked publishers (and exclusions), then ads, then a short campaign config (budget, split, CPM range).
