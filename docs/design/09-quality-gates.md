# Design 09 — Quality gates (fact-check + anti-hallucination)

Status: **agreed** (2026-06-18). PRD Phase 13. Runs inside Stage C (Design 01, step 4);
feeds the GEO citability score (Design 03).

## Reflection (anti-hallucination)

- **Iterative until target (max N).** Reflect → revise → re-check, looping until the
  grounding target is met or a max-iteration cap (prevents runaway cost). Each pass
  critiques the draft against gathered sources, tags claims grounded/unsupported, and
  strips or flags unsupported ones.
- Emits a **grounding report** surfaced claim-by-claim in the editor (each claim shows
  grounded/unsupported + its source).

## Fact-checking

- **Sources+KB first, escalate to web.** Verify claims against already-gathered research
  sources + the brand KB; spend a fresh `web_search` only on claims with **no** supporting
  source. Balances rigor and cost.

## Gating

- **Warn, don't block** (from Design 01) — grounding score + flags shown; editor can still
  publish. Targets are advisory.

## Open / deferred

- Max reflection iterations N + grounding target value (tune at build).
- Claim-extraction method (LLM segments the draft into atomic claims) — build-time.
