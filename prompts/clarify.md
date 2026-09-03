The brief extracted from an advertiser's description came back low-confidence. Generate at most 2 multiple-choice clarifying questions that would most improve it. This is the only chance to ask — the system proceeds with its best guess after one round, so make each question count.

Brief extracted so far (as JSON): {brief_json}
Fields the extractor flagged as guessed rather than stated: {ambiguity_flags}

Rules:
- Each question MUST be multiple choice with 3-6 concrete options — never an open text box. An advertiser answers by picking one, not by typing.
- If `category` is one of the ambiguous fields, draw its options from the catalog's real categories: {categories}.
- Every question needs a `why`: one plain sentence explaining what's missing and why it matters for matching publishers or personas (e.g. "Your description didn't name a price point, which we need to check budget compatibility with each publisher's typical order size.").
- Order questions by how much they'd improve confidence — most valuable first.
- Prefer a single question if it would resolve most of the ambiguity by itself. Only ask two if there are genuinely two independent things worth clarifying (e.g. category is unclear AND price is unclear) — do not pad to 2 for its own sake.
- Never ask about a field that wasn't flagged as ambiguous.
