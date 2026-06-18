# Design 05 — Article-type template system

Status: **agreed** (2026-06-18). PRD "Article-type template system". One
`content_templates` registry (data, not code), selected at article/brief/campaign
creation; drives the outline and GEO-optimize stages.

## Template fields

```
content_templates:
  type              # listicle | how_to | checklist | qa | versus | roundup |
                    # ultimate_guide | news | youtube_article | interactive_tool
  outline           # STRUCTURED section-spec (below)
  schema_org_type   # Article | FAQPage | HowTo | ItemList | NewsArticle | WebApplication
  default_word_count
  geo_target        # target GEO score band (e.g. qa → 85)
  pipeline_stages   # which Stage C steps run (+ special: widget_gen, transcript_ingest)
  enabled
```

## Decisions

- **Structured section-spec** (not freeform prompt). Each outline is machine-readable
  sections so we get per-section regeneration, validation, and consistent structure:
  ```
  outline.sections: [
    { role, heading_pattern, min_words, max_words, repeat?: {min,max}, prompt_notes? }
  ]
  ```
  e.g. Listicle = intro + `{repeat: ranked_item}` + conclusion; How-to = intro +
  materials + `{repeat: step}` + tips + FAQ.
- **Global templates + KB brand voice.** One shared curated set of 10 templates;
  per-brand differences come from each `business_profile`'s brand KB (voice/style),
  **not** template forks. Admins clone/edit templates in the UI.
- Special types toggle extra pipeline stages: `interactive_tool` → widget-generation;
  `youtube_article` → transcript-ingestion. Standard types run vanilla Stage C.
- Template feeds **outline stage** (structure) and **GEO-optimize stage** (schema.org
  type + which extractable patterns to favor — QA leans into FAQ blocks, How-to into
  HowTo steps).

## Open / deferred

- Seed the 10 default templates' exact section-specs (build-time, iterate with real output).
- Template editor UI (admin) — M2+.
