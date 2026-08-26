# PowaCRM

**Open-source outbound sales automation platform, built on [Powabase](https://docs.powabase.ai).**

PowaCRM is a lite CRM plus an outbound engine: it sources leads from Apollo (or
CSV), researches every company with AI agents, drafts hyperpersonalized
LinkedIn + email touches, and runs them through multi-step sequences under
human-controlled guardrails — auto-stopping the moment a lead replies so a
human takes over. It's a real outbound tool *and* an open-source example of
building a **backend-less** product on the Powabase AI BaaS.

PowaCRM is **not a standalone app** — it's a consumer of a **Powabase project**,
which provides its database, auth, agents, workflows, and webhook endpoints.
Unlike its sibling [`rankforge/`](../rankforge), PowaCRM ships **no server of its
own**: the React SPA talks straight to the project's PostgREST API (Anon key +
RLS), and every pipeline job — Apollo sourcing, AI research, message drafting,
the sequence-engine tick, Dux Soup / SendGrid webhook ingestion — runs inside
Powabase workflows and agents.

## What it does

1. **Lead sourcing** — Apollo ICP search (per-brand filters) and CSV import with
   column auto-mapping and dedupe-then-restore semantics.
2. **AI research** — a researcher agent (`web_scrape`/Firecrawl +
   `web_search`/Exa) profiles each company: stack signals, why-now angle,
   personalization hooks, and a 0–100 fit score.
3. **Hyperpersonalized drafting** — a copywriter agent turns brand voice +
   research + thread history into per-lead messages with a self-scored
   confidence; low-confidence drafts route to a human review queue.
4. **Sequences** — versioned multi-step LinkedIn + email sequences (steps are
   drafting prompts, not templates); enrollments freeze a snapshot of the steps
   so mid-flight edits are safe.
5. **Execution** — LinkedIn via the Dux Soup Turbo remote-control queue API,
   email via SendGrid, under daily caps, quiet hours, and a per-brand
   dry-run/pause switch.
6. **Reply handling** — Dux Soup / SendGrid webhooks feed a unified activity
   timeline; any reply halts the sequence and flags the lead for takeover. The
   AI never auto-replies.
7. **Lite CRM** — pipeline kanban, lead records with inline editing and a
   month-grouped timeline, multi-brand workspace. Schema and UX conventions
   are borrowed from [Twenty](https://github.com/twentyhq/twenty) (documented
   per-pattern in the design spec).

See [`docs/2026-08-26-powacrm-design.md`](docs/2026-08-26-powacrm-design.md)
for the full design and
[`docs/2026-08-26-phase1-foundation-plan.md`](docs/2026-08-26-phase1-foundation-plan.md)
for the phase 1 implementation plan.

## Architecture

```
React SPA (app/) ── Anon key + GoTrue JWT ──▶ Powabase PostgREST (public.* CRM tables, RLS)
      │                                        Powabase Auth (GoTrue)
      │                                        Powabase Realtime (events/touches)
      └── deployed workflow webhooks ────────▶ Powabase workflows ──▶ Apollo · Dux Soup · SendGrid
                                               Powabase agents (researcher, copywriter)
```

| Dir | Stack | Purpose |
|---|---|---|
| `app/` | React 18 · Vite · TypeScript · TanStack Query · supabase-js | The dashboard / lite CRM (the only code that runs outside Powabase) |
| `db/` | SQL migrations + psql test scripts | Schema, RLS, RPCs — applied over the project's Database URL |
| `docs/` | — | Design spec + implementation plans |

Secrets live in per-app `.env` files (gitignored); third-party API keys
(Apollo, Dux Soup, SendGrid) live only in Powabase workflow/tool configs,
never in this repo or the browser bundle. The SPA holds the Anon key only.

## Status

Under active development. Phase 1 (schema + RLS + auth + lite CRM: kanban
board, lead view, CSV import) is specified in `docs/` — later phases add the
research agents, Apollo sourcing, the sequence engine, and the LinkedIn/email
execution legs.
