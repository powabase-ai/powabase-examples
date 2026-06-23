# Design 02 — Research & SERP intelligence (Stage A)

Status: **agreed** (2026-06-18). PRD Phase 1. Feeds the brief (Stage B) and everything
downstream; reused by scouts and programmatic SEO.

## What it gathers

Input: `topic` + the brand's `business_profile` (niche, competitors, audience, locale).
A Powabase **agent** (ReAct + `web_search`/Exa + `web_scrape`/Firecrawl) returns a
strict `ResearchResult` JSON, persisted to `research_runs`.

1. **SERP analysis** — top organic results (title/URL/snippet), related queries, PAA.
2. **Competitor teardown** — scrape top-ranking pages → heading outline, word count,
   subtopics/entities, schema present, freshness. The content-gap map (what + how long).
3. **Keyword expansion + intent** — secondary/semantic keywords, clusters, intent class.
4. **Answer-engine reconnaissance (novel, GEO)** — what AI answer engines currently say
   about the topic and **which sources they cite** → the GEO citation gap.

## Decisions

- **Default depth = Deep** (overridable per run): ~20 SERP results, scrape ~10
  competitors, full keyword pass, + answer-engine recon. Presets Quick/Standard/Deep
  also selectable (e.g. programmatic runs use Quick).
- **Keyword metrics = native proxies now, pluggable later.** No true volume/difficulty
  yet — use PAA, related searches, autocomplete, competitor term-frequency. Define a
  `KeywordMetricsProvider` interface so DataForSEO/SerpApi/etc. drops in later without
  touching callers.
- **Answer-engine recon = designed now, built in M3** (with GEO scoring). Until then,
  "Deep" runs everything except AE recon; AE recon switches on in M3.
- **Agent, not workflow** — research is open-ended (how many competitors to scrape,
  which threads to follow); ReAct fits. Strict structured output keeps storage clean.

## ResearchResult schema (first cut)

```
{
  topic, locale, intent,
  serp: [{ rank, title, url, snippet }],
  paa: [string],                          // People Also Ask
  related_queries: [string],
  competitors: [{ url, title, word_count, headings: [{level, text}],
                  entities: [string], has_schema: bool, published_at }],
  keyword_clusters: [{ label, keywords: [string], intent }],
  answer_engine: {                        // M3
    engines: [{ name, answer_summary, cited_sources: [url] }],
    citation_gap: [string]
  }
}
```

## Open / deferred

- Real keyword-metrics provider (paid API) — when prioritization needs true volume.
- AE recon implementation (which engines: Perplexity API? scrape Google AI Overview via
  Firecrawl?) — designed in 03-geo, built M3.
- Research caching/dedupe window (topic+locale) to avoid re-running — build-time.
