# Design 01 — Generation pipeline (the spine)

Status: **agreed** (2026-06-18). Implements PRD Phase 3 (+13). One design note per
key feature; this is the spine the others plug into.

## Shape: segmented, backend-orchestrated

The editorial human gates rule out one monolithic workflow (workflows run
start→finish; they don't pause for a human). Our FastAPI backend orchestrates three
stages, persisting article state between them. The autonomous stretch (Stage C) is a
single Powabase Workflow, reused by manual creation, scouts, and programmatic SEO.

```
Stage A: RESEARCH        Stage B: BRIEF           Stage C: GENERATE (one Powabase Workflow)
agent + web_search/      LLM builds brief         starter → outline → draft(per-section)
web_scrape               from research            → reflect/fact-check → SEO-opt → GEO-opt → response
   │ research_run           │ brief                   │ article (status=draft)
   ✋ gate 1                 ✋ gate 2                  ✋ gate 3 (edit → approve → publish)
   (pick angle/             (steer keywords/
    competitors)             headings/outline)
```

## Decisions

- **Human gates (manual flow): 3** — after research (pick angle/competitors), after
  brief (steer keywords/headings/outline), after draft (edit → approve → publish).
- **Scout flow (L3): 0 gates** — A→B→C run autonomously, brief auto-accepted, article
  lands at `in_review` (which IS the human's draft-review gate). Same machinery.
- **Drafting: per-section loop** — generate section-by-section against the outline,
  each grounded in brand KB + research sources. Enables single-section regeneration
  (FR-3.5) and handles 3000+ words. Cost: more LLM calls (acceptable).
- **Grounding: warn, don't block** — reflect/fact-check computes a grounding score and
  flags unsupported claims in the editor; the editor can still publish. Humans stay in
  control.
- **Stage C is single-responsibility** — its only input is a brief (+ KB id + research
  refs + article-type template). Three callers feed it: manual UI, scouts, programmatic.

## Stage C block graph (conceptual — verify block types vs Powabase workflow docs)

1. `starter` — receives `{brief, research_refs, kb_id, template}`
2. **outline** — structure from brief + the article-type template's outline pattern
3. **draft (per-section loop)** — write each section grounded in KB + sources; inline
   citations to research sources
4. **reflect / fact-check** — critique draft vs sources, flag/strip unsupported claims
   (anti-hallucination, FR-13); emit grounding report
5. **SEO-optimize** — meta title/desc, heading hierarchy, keyword placement,
   internal-link slots
6. **GEO-optimize** — Q&A blocks, schema.org JSON-LD, citable structure
7. `response` — assembled article payload → persisted as `articles` row (status=draft)

> Powabase workflows have exactly 10 block types (`starter`/`response`, not
> `input`/`output`/`llm`). Map stages 2–6 to real block types when building M2 —
> verify against the workflows reference + live docs.

## Open / deferred

- Exact block-type mapping for stages 2–6 (build-time).
- Whether reflect and fact-check are one block or two.
- Section-regeneration UX (re-run a single outline node) — M3 editor work.
