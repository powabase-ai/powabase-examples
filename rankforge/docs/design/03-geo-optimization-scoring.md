# Design 03 — GEO optimization & scoring (the differentiator)

Status: **agreed** (2026-06-18). PRD Phase 4. The core differentiator: make content
citable by AI answer engines, and measure how citable it is.

## GEO optimization (Stage C step 6; also re-runnable on-demand in the editor)

Transforms a draft to be answer-engine-citable:
1. **Direct-answer leads** — each H2/question opens with a tight 40–60-word extractable
   answer before elaboration.
2. **Q&A structure** — FAQ blocks aligned to PAA + AE-recon questions → `FAQPage` JSON-LD.
3. **Citable claims** — standalone, quotable, source-attributed, specific numbers/data.
4. **Entity clarity** — explicit definitions, canonical names, salient attributes.
5. **Structured data** — schema.org JSON-LD per article-type (Article/FAQPage/HowTo/…).
6. **Authority + freshness** — primary-source citations, dates, current data, E-E-A-T.
7. **Extractable formatting** — lists/tables/clean headings.

## GEO score (0–100, with explanations + fix suggestions)

Sub-signals: direct-answer presence · citable-claim density · cited-source authority ·
entity coverage vs brief · structured-data validity · question coverage · extractability.

## Decisions

- **Hybrid scoring** — deterministic checks for measurable signals (JSON-LD validates?
  lead-answer length? lists present? entity strings/embeddings found?) + LLM judgments
  for fuzzy ones (is this a good direct answer? is this claim citable?). Same engine
  scores SEO (Feature 5). Cheap, consistent, auditable.
- **Advisory + per-type targets** — score shown with fixes and a target band per
  article-type (e.g. QA → 85+), but **never blocks publish** (consistent with
  warn-don't-block grounding). Targets live in `content_templates`.
- **Citability test = design now, build post-M3** — a future validation that feeds our
  draft to a live engine (Perplexity API / scrape AI Overview) and checks if it would
  cite us. Captured here; not in M3 scope.
- **AE recon (Feature 2) is the baseline** the score measures against — are we more
  citable than the sources cited today?

## Score object (stored on `articles.geo_score`)

```
{ total: 0-100, target: 85, met: bool,
  signals: [{ key, score, weight, explanation, fixes: [string], method: 'deterministic'|'llm' }] }
```

`articles.seo_score` uses the same shape (Feature 5). Both shown side-by-side in the editor.

## Open / deferred

- Exact signal weights per article-type (tune with real articles).
- Citability-test engine choice + cost model (post-M3).
- JSON-LD generation: templated per type vs LLM-authored then validated (build-time;
  lean templated + deterministic validation).
