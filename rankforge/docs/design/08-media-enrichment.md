# Design 08 — Media enrichment

Status: **agreed** (2026-06-18). PRD Phase 11. M7.

## Images — brand image library (user-curated)

- Each `business_profile` has its own **image library** that users **upload and maintain**
  themselves, stored in Powabase **Storage**. Brand-consistent, rights-clean, no synthetic
  look.
- The draft/editor selects appropriate images from the library per slot (hero + inline),
  matched by tags / embedding / filename + caption; auto-generates **alt-text** (SEO +
  accessibility).
- AI-generation and stock APIs (Unsplash/Pexels) are **deferred optional sources** that
  can supplement the library later — not v1.

## YouTube → article + related videos

- **Official YouTube Data API** (needs a Google API key) for search / related-video lookup
  + a **transcript library** for captions. Reliable and ToS-friendly (vs scraping).
- Flow: URL → fetch transcript → generate (how-to/summary, `youtube_article` template) →
  embed video (`VideoObject` schema). Related-video suggestions embeddable in any article.

## Interactive SEO mini-tools

- **LLM-generated, sandboxed.** LLM generates self-contained HTML/JS widgets
  (calculators, quizzes, checklists) from a **curated tool-template library**; rendered in
  a **sandboxed iframe (isolated origin)** so generated JS can't touch the app.
- Doubles as a **linkable asset** (Design 06 backlinks). Tool bundle stored in Storage.

## Decisions

- Images = **user-uploaded brand library** (primary); AI-gen/stock deferred.
- YouTube = **official YouTube Data API + transcript lib** (needs Google API key — Studio
  handoff for the key).
- Mini-tools = **LLM-generated, sandboxed iframe**, from a template library.

## Data model

- `media_assets` — `business_id`, storage_path, type, tags jsonb, caption, alt_text,
  embedding?. (the image library)
- `interactive_tools` — `business_id`, `article_id?`, template_key, html_bundle_path,
  config jsonb.

## Open / deferred

- Image auto-match strategy (tags vs embedding vs both) — build-time.
- iframe sandbox CSP specifics.
- AI-gen / stock image sources (later).
