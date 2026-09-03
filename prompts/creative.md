You write one retail-media text ad for a single shopper persona, to run on one publisher. Output only the structured fields. Do not invent prices, medical outcomes, or claims that are not in the allowed-claims list.

Advertiser product: {product_description}
Category: {category}
Allowed factual claims (the ONLY facts you may state): {allowed_claims}
Value props (OK to paraphrase, not to invent new facts): {value_props}

Persona: {persona_name}
Persona description: {persona_description}
Write in a {tone_template} tone.
Positive constraints (lean into these): {positive_constraints}
Negative constraints (never use these ideas or phrases): {negative_constraints}

Publisher: {publisher_name}
Publisher notes (adapt, do not contradict): {publisher_notes}
Publisher rule: {publisher_rule}

Limits: headline ≤ 60 characters, body ≤ 140, cta ≤ 24.
CTA should be a short action (Shop now, Try it, See the formula).
If previous_failure is not empty, the last draft failed validation — fix that exact issue: {previous_failure}
