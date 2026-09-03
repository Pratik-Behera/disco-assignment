You extracted a first-pass brief from an advertiser's description, but confidence was too low to proceed, so the advertiser was asked one clarifying question. Incorporate their answer and produce a corrected, complete brief — you are re-running the same extraction, not patching one field in isolation, so fill in every field again using everything you now know.

Original description: {raw_input}

First-pass brief (for reference — it may contain guesses you should now correct or firm up): {previous_brief_json}

Clarifying question asked and the advertiser's answer:
{answers}

Rules:
- Update every field the answer touches, not just the one the question was literally about — a category answer often changes what subcategories or price range are plausible too.
- Raise `confidence` to reflect your genuinely improved certainty from the answer.
- If the answer still leaves real ambiguity (it doesn't fully resolve everything), keep `confidence` honest — it's fine for it to remain below 0.6. The system proceeds with your best guess either way and flags it as low-confidence in the UI, so there is no benefit to overstating certainty here, and a false-confident guess is worse than an honest flagged one.
- Keep `ambiguity_flags` accurate: drop fields the answer resolved, keep any that are still genuinely guessed.
