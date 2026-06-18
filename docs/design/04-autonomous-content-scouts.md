# Design 04 — Autonomous content scouts (semi-long-running agents)

Status: **agreed** (2026-06-18). PRD Phase 9. The headline capability. A scout is a
scheduled discovery run (Powabase Workflow + cron trigger) scoped to one
`business_profile`. Reuses the Stage A→B→C pipeline (Design 01).

## Signal collection (native tools only — replaces Google Trends)

- **News momentum** — Exa `web_search` with recency filters over the niche → rising stories.
- **Competitor new-content** — fetch competitors' `sitemap.xml`, diff vs last run → new URLs.
- **Rising subtopics** — PAA / related-query deltas from research.
- **SERP volatility** *(designed, deferred)* — track target-keyword ranks over time via
  stored SERP snapshots, diffed. Strong signal but recurring scrape cost → later phase.

## Run pipeline

```
collect signals → dedup → score → write opportunities → (L3) auto-draft top-N → notify
```

- **Dedup** — embedding similarity vs existing `articles` + past `opportunities` (incl.
  dismissed), so ideas don't resurface. Threshold tuned at build.
- **Score** — niche-relevance × momentum × competition-gap × intent. LLM judges
  relevance/intent; deterministic for momentum (recency/volume) + gap (do we already
  cover it?).
- **Auto-draft (L3)** — opportunities above a **score threshold**, capped at **N per run**
  (e.g. top 3), auto-run A→B→C → article staged at `in_review`. Rest sit in the inbox.

## Decisions

- **Cadence = daily** (per-scout overridable). Auto-draft still gated by score + cap.
- **Auto-draft = threshold + per-run cap** — controls cost/quality; inbox holds the rest.
- **SERP-volatility = deferred** — M5 ships News + competitor-diff + subtopic signals;
  rank-tracking snapshots follow.
- **Autonomy = L3** (from PRD): draft + full SEO/GEO scoring → `in_review`. Never
  auto-publishes. Configurable per scout (L1/L2/L3).

## Guardrails (autonomous needs brakes)

- Per-scout daily auto-draft cap; score threshold to draft.
- Credit-aware: `402 insufficient_credits` → pause scout + alert, do NOT retry.
- Auto-pause after repeated run errors; surface in scout dashboard.
- `429` on workflow execute → back off with jitter.

## UX

- **Opportunity inbox** — cards: topic, "why now" signal, score, suggested type, source
  evidence; actions Draft · Dismiss · Snooze · Open. Dismissed feed dedup.
- **Scout dashboard** — per-scout health, last run, found-vs-converted, content calendar.
- **Notifications** — in-app first (new high-value opportunity / staged draft); email/Slack later.

## Open / deferred

- Score threshold + cap defaults (tune with real runs).
- SERP-volatility tracking build (later scout phase).
- Notification digest vs per-event (default per-event inbox; digest later).
- Whether the discovery run is one workflow or backend-orchestrated collectors feeding a
  workflow (build-time; lean backend collectors → one scoring/draft workflow).
