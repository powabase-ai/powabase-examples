# PowaCRM — Design Spec

**Date:** 2026-08-26 (rev 2 — Twenty architecture review folded in; rev 2.1 — repackaged as a powabase-examples app)
**Status:** Approved design, pending implementation plan
**Repo:** `powabase-ai/powabase-examples` → `powacrm/` (open-source example app, sibling of `rankforge/`)
**First brand:** gpt-trainer (multi-brand from day one; brand data is local seed, never committed)

## 1. Purpose

A custom dashboard + pipeline that automates and hyperpersonalizes outbound
for any brand, starting with gpt-trainer. It replaces the competitor's
Chatbase/Claude/Attio/Apollo/LinkedIn stack with:

- **Apollo** — ICP search (sourcing) + contact enrichment, via API.
- **Dux Soup Turbo** — LinkedIn execution layer via its remote-control queue
  API + webhooks. We do NOT build our own LinkedIn automation.
- **SendGrid** — email channel (existing account/sender identity).
- **Powabase** — the entire backend: Postgres/PostgREST/RLS (lite CRM),
  agents (research + copywriting), workflows (integrations, webhooks,
  sequence engine). No Attio: the dashboard IS the CRM.
- **React SPA** — dashboard / lite CRM / review cockpit.

Schema and UX conventions are deliberately borrowed from **Twenty**
(github.com/twentyhq/twenty), reviewed 2026-08-26; each borrowed pattern is
marked `[Twenty]` below. We borrow patterns, not code or infrastructure —
their metadata-driven schema engine, ORM, GraphQL layer, and Jotai/Apollo
state machinery are explicitly out (we have a fixed domain + PostgREST).

Decisions locked during brainstorming:

| Question | Decision |
| --- | --- |
| Lead sources | Apollo ICP search + CSV/list import |
| Autonomy | Auto-send with guardrails (caps, confidence threshold, review queue) |
| LinkedIn execution | Dux Soup Turbo remote-control API (user will upgrade if needed); no internal clone |
| Channels | LinkedIn + email from day one |
| CRM | Own lite CRM in Powabase; Attio dropped (mirror possible later) |
| Replies | Auto-stop sequence + flag for human takeover; AI never auto-replies |
| Architecture | Powabase-native (Approach A) |

## 2. Architecture

```
React SPA (dashboard)
  │  anon key + GoTrue JWT          ┌────────────────────────────┐
  ├──── PostgREST /rest/v1 ────────►│ Powabase project           │
  │  (CRM data, RLS on)             │  public schema (CRM)       │
  ├──── workflow webhooks ─────────►│  workflows (pipeline)      │
  │  (trigger research/approve)     │  agents (research/copy)    │
  │                                 └──────┬─────────────────────┘
  │                                        │ general_api blocks
  │                    ┌───────────────────┼───────────────────┐
  │                    ▼                   ▼                   ▼
  │               Apollo API        Dux Soup queue API      SendGrid
  │                                        │                   │
  └── Realtime (events/touches) ◄── webhook workflows ◄── event webhooks
```

- All third-party secrets (Apollo key, Dux userid/auth, SendGrid key) live
  only in workflow block configs / agent tool configs server-side. The SPA
  ships only the anon key; RLS is enabled on every `public` table.
- Auth: single GoTrue user (the founder). RLS policies: `authenticated` role
  only, always filtered `deleted_at IS NULL`. Dashboard-triggered pipeline
  actions call deployed workflow webhooks (Bearer webhook secret).
- Dux Soup webhooks and SendGrid event/inbound-parse webhooks POST to
  deployed workflow webhook URLs (`/api/webhooks/{id}`) — public URLs for
  free, no tunneling. Webhook runs are synchronous with a 5-minute cap;
  ingestion workflows must stay fast (normalize + insert only).

## 3. Data model (`public` schema, RLS on)

### 3.1 Cross-cutting conventions `[Twenty]`

Applied to every table:

- `id uuid primary key default gen_random_uuid()`; **deterministic UUIDv5
  where idempotency matters** (see enrollments, §3.2).
- `created_at` / `updated_at timestamptz not null default now()`, with a
  shared `set_updated_at()` BEFORE UPDATE trigger (Twenty maintains
  `updatedAt` in the app layer; with PostgREST it must be a trigger).
- **Soft delete**: `deleted_at timestamptz`; RLS policies filter
  `deleted_at IS NULL`. Junction/re-creatable tables get partial unique
  indexes `WHERE deleted_at IS NULL`; people/companies keep full unique
  indexes so a re-import **restores** the soft-deleted row instead of
  duplicating it (dedup-then-restore, not dedup-then-insert).
- **Actor provenance**: `created_by_source text not null default 'MANUAL'
  check (created_by_source in
  ('MANUAL','API','WORKFLOW','AGENT','IMPORT','WEBHOOK','SYSTEM'))` +
  `created_by_name text` + `created_by_context jsonb`. Every row says who
  made it — human, agent, workflow, or import.
- **Enums as `text + CHECK`**, never PG enum types (painful to alter,
  awkward through PostgREST). Ordered/colored picklists (stages) live in a
  small options table carrying `{value, label, color, position}`.
- **Kanban ordering**: `position double precision not null default 0`,
  updated by fractional midpoint (`(prev+next)/2`) on drag — one PATCH per
  drop, no sibling reindexing. (Improves on Twenty, which only supports
  first/last.)
- **Full-text search**: `search_vector tsvector GENERATED ALWAYS ... STORED`
  + GIN index on people and companies, built with an IMMUTABLE `unaccent`
  wrapper function (the load-bearing trick — plain `unaccent()` is STABLE
  and can't be used in a generated column).
- Money (if ever needed): `amount_micros numeric` + `currency_code text`.
- Multi-value contact fields flatten to `primary + additional_* jsonb`
  columns (e.g. `email text` + `additional_emails jsonb`), not a jsonb blob
  and not a child table.

### 3.2 Tables

- **`brands`** — name, product description, voice/persona notes, ICP
  definition (jsonb, stored as Apollo search filters), daily caps
  (connects/messages/emails), quiet hours + timezone, `dry_run` flag,
  `paused` flag, confidence threshold.
- **`companies`** — brand_id, name, **`domain text`** (normalized
  registrable domain, lowercased — `UNIQUE (brand_id, domain)`; the display
  URL is a separate column) `[Twenty — they dedupe on raw URL and it leaks]`,
  apollo_org_id, industry, headcount, tech-stack signals (jsonb), research
  summary, linkedin_url, search_vector.
- **`people`** — company_id, brand_id, first_name/last_name, title,
  linkedin_url, **`email text`** (normalized lowercase;
  `UNIQUE (brand_id, lower(email))` partial where non-null) +
  `additional_emails jsonb`, apollo_person_id, enrichment payload (jsonb),
  fit_score, position, search_vector, **stage** (text + CHECK):
  `sourced → enriched → researched → in_sequence → replied → won |
  disqualified`. Partial uniques also on (brand_id, apollo_person_id) and
  (brand_id, linkedin_url).
- **`sequences`** — brand_id, name, `active_version_id`. Steps live on
  **`sequence_versions`** `[Twenty workflowVersion]`: `steps jsonb` (ordered
  array of `{id, channel, day_offset, drafting_prompt, stop_conditions,
  retry_on_failure, continue_on_failure}`), status
  `DRAFT|ACTIVE|ARCHIVED`, with
  `UNIQUE (sequence_id) WHERE status = 'ACTIVE'`. Publishing creates a new
  version; versions are immutable. Per-step error policy is in the step,
  not global `[Twenty]`.
- **`enrollments`** — **`id = uuid_generate_v5(ns, sequence_id:person_id)`**
  so enrollment is `INSERT ... ON CONFLICT DO NOTHING` and replay-safe
  `[Twenty campaign engine]`. Columns: person_id, sequence_version_id,
  **`flow jsonb`** (frozen snapshot of the version's steps at enrollment —
  mid-flight sequence edits never corrupt in-flight enrollments `[Twenty]`),
  current_step, status
  (`active | awaiting_approval | replied | completed | stopped | failed`),
  `next_action_at`.
- **`touches`** — real rows per attempt (not jsonb — the poller needs
  "what's due"): enrollment_id, person_id, step_id, channel
  (`linkedin_connect | linkedin_message | email`), ai_draft, final_text,
  confidence, status (`drafted → held_for_review | approved → queued →
  sent | failed | skipped | cancelled`), attempt_count,
  `provider_message_id` (Dux `messageid` / SendGrid id;
  `UNIQUE (channel, provider_message_id)` partial — idempotent webhook
  correlation `[Twenty]`), delivery_status
  (`queued|sent|failed|bounced|complained|skipped`) `[Twenty]`, sent_at.
  Poller index: `(status, next_action window)` partial on `approved`.
- **`events`** — the timeline `[Twenty timelineActivity]`: person_id
  (+ nullable company_id), event_type (FK to **`event_types`** lookup:
  name, verb, label, icon), **`happens_at timestamptz not null`** (display
  time, distinct from `created_at`), `properties jsonb` (incl.
  `{diff: {field: {before, after}}}` for record edits),
  `linked_record_id` + `linked_record_kind` + **`linked_record_cached_name`**
  (denormalized so the feed renders with zero joins), actor columns
  (source/name), source payload (jsonb). Index
  `(person_id, happens_at DESC)` — the query is always "target + time desc"
  (an index Twenty itself forgot). Repeated events with the same
  (person, actor, event_type) within **10 minutes collapse** by
  UPDATE-merging the diff (keep oldest `before`, newest `after`) `[Twenty]`.
- **`suppressions`** `[Twenty messageSuppression — borrowed verbatim]` —
  email_address, reason (`unsubscribe|bounce|complaint|manual`), source,
  brand_id. Two partial uniques: `UNIQUE (brand_id, email_address) WHERE
  topic IS NULL` (global block) and `UNIQUE (brand_id, email_address,
  topic) WHERE topic IS NOT NULL`. Checked before every email dispatch; a
  LinkedIn analog (`do_not_contact` on people) covers that channel.
- **`import_batches`** — filename, row_count, status, `errors jsonb`
  (partial-failure reporting — Twenty lacks this). Imported rows get
  `created_by_source='IMPORT'` + `created_by_context = {import_id,
  filename, row}` so an import is auditable and reversible with one
  DELETE `[Twenty]`.
- **`views`** `[Twenty View, radically simplified]` — name, object
  (`people|companies`), type (`table|kanban`), `filters jsonb`,
  `sorts jsonb`, `visible_fields jsonb`, `group_by_field`. Kanban columns
  derive from the stage options table, not from view rows.

FK delete policy `[Twenty]`: lookups/ownership → `ON DELETE SET NULL`;
child/junction rows (touches, events, enrollments) → `ON DELETE CASCADE`.

## 4. Pipeline

### 4.1 Sourcing — `wf-apollo-source`
Manual trigger (dashboard) or weekly cron. Brand ICP filters → Apollo
`people/search` (`general_api`) → dedupe (normalized domain/email/LinkedIn
URL; soft-deleted matches are **restored**, not re-created) → insert
companies + people at `sourced`, `created_by_source='WORKFLOW'`. Freemail
and group addresses (`noreply@`, `info@`) are filtered out `[Twenty
contact-creation-manager]`. CSV import bypasses workflows: SPA parses and
bulk-inserts via PostgREST under an `import_batches` row.

### 4.2 Enrichment — `wf-enrich`
Cron (~30 min), batches `sourced` leads through Apollo `people/match`
(email, verified title, org facts). Enrichment failure does not kill a
lead — LinkedIn-only sequencing still works. Stage → `enriched`.

### 4.3 Research — `researcher` agent (via `wf-research`)
Cron batch + on-demand per lead. Tools: `web_scrape` (Firecrawl; company
homepage/pricing/docs — the "what tools do they use, would the product
actually help" analysis), `web_search` (Exa; news/funding/hiring),
`database_query`. Structured output: company summary, stack signals,
why-now angle, 2–3 personalization hooks, fit score 0–100. Saved to
`companies.research` + an `events` row (actor source `AGENT`). Stage →
`researched`; below fit threshold → `disqualified` (resurrectable in UI).
Prereq (Studio): `FIRECRAWL_API_KEY`, `EXA_API_KEY` under Settings → Tools.

### 4.4 Drafting — `copywriter` agent (via `wf-draft`)
On enrollment (manual or auto-rule, e.g. fit ≥ 70) and ahead of each due
step. Inputs: brand voice + product notes, research hooks, step prompt
(from the enrollment's frozen `flow`), thread history (prior touches +
events). Output: message draft + self-scored confidence. Constraints:
LinkedIn connect-note char limit (~300); founder-voice style rules (no
competitor bashing). Confidence ≥ threshold → `approved`; else
`held_for_review`.

### 4.5 Sequence engine — `wf-sequence-tick`
Cron every 15 min. For `active` enrollments with due `next_action_at` and
an `approved` touch: check brand pause flag, daily caps, quiet hours,
suppression list → dispatch:
- LinkedIn: POST to Dux Soup queue
  (`https://app.dux-soup.com/xapi/remote/control/{userid}/queue`) — `view`,
  `invite` (with personalized note), `message` commands; our touch id
  correlates with Dux `messageid` in webhook events.
- Email: SendGrid send from the existing sender identity.
Then: touch → `queued`/`sent`, schedule next step's `next_action_at`,
pre-trigger `wf-draft` for the next step. Cap reached → hold until next
window (no burst).

Error handling `[Twenty campaign engine]`: **classify retryability** —
temporary/unknown provider errors leave the touch retryable (up to 3
attempts, `attempt_count`); terminal errors (invalid address, suppressed,
4xx) mark it `failed`/`skipped` immediately, no retry. Per-step
`continue_on_failure` decides whether the enrollment proceeds or fails. A
**staleness sweeper** (same cron) flags enrollments stuck mid-state > 1h.
Enrollment completion is **derived from touch counts**, not a maintained
counter; an enrollment whose touches include failures completes as
`completed_with_errors`. Nothing stalls silently.

### 4.6 Event ingestion — `wf-dux-events`, `wf-sendgrid-events`
Deployed webhooks. Dux Soup: visit events (incl. scraped profile data —
saved as free enrichment), invite sent, connect accepted, and incoming
LinkedIn messages via Message Bridge. SendGrid: delivered/open/click/
bounce + inbound-parse replies. Both normalize into `events`, correlate by
`provider_message_id` (unique index makes double-delivered webhooks
idempotent), and apply rules:
- **Reply (either channel)** → enrollment `replied`, pending touches
  `cancelled`, lead stage `replied`, flagged for human takeover.
- **Connect accepted** → skip ahead to the first message step.
- **Bounce/complaint** → touch delivery_status updated + email address
  added to `suppressions`; email leg paused for that lead.
Webhook workflows do minimal work inline (5-min sync cap): validate,
normalize, insert, update.

## 5. Dashboard (React + Vite SPA)

**Data layer** `[Twenty — explicitly NOT their approach]`: TanStack Query +
`postgrest-js`. Optimistic updates via `onMutate` snapshot →
`onError` rollback → `onSettled` invalidate — this replaces the ~400 LOC of
hand-rolled Apollo cache repair Twenty needed. Plain React state/context
for UI state. Realtime subscriptions on `events`/`touches` for live reply
notifications (requires the `supabase_realtime` publication).

**Design system** `[Twenty tokens, one tokens.css]`: 4px spacing scale,
Inter, Radix 12-step gray scales with semantic aliases
(`--bg-primary/secondary/tertiary`, `--fg-primary/secondary/tertiary/light`,
`--border-light/medium/strong`), radius sm/md/lg, light+dark via `.dark`
overrides. Signature moves worth keeping: floating white content card on a
gray app background; hover = transparent-light background everywhere;
field labels + timestamps in tertiary; hairlines in `border-light`; stage
tags & kanban headers use Radix step-3 background / step-11 text.

**Screens:**

- **Pipeline board** — kanban by stage `[Twenty record board]`: columns
  from the stage options table (color/label/position), per-column count,
  drag between columns = one PATCH of `{stage, position}` with fractional
  midpoint position; compact cards. Table view with the same saved-view
  model (filters/sorts/visible fields in `views`). Brand switcher at top.
- **Lead view** `[Twenty record show page]` — hardcoded two-pane grid
  (~320px left, rest right). Left: summary card (avatar, inline-editable
  name, "Added X ago" with exact-date tooltip) + inline-editable field list
  (icon / fixed-width tertiary label / value; hover bg; "Empty"
  placeholder; Enter/blur saves, Esc reverts). Right: tabs
  `Activity | Messages | Research | Notes`.
- **Activity timeline** `[Twenty timeline]` — grouped by **month** with a
  hairline separator, 16px event-type icon per row + 2px connector line,
  sentence-shaped rows ("Researcher scored Acme 82"), relative time with
  exact-timestamp tooltip, multi-field record edits collapsed behind an
  "N fields" toggle expanding to a before→after diff card.
- **Review queue** — held-for-review drafts with research context inline;
  edit-in-place, approve, skip, regenerate; batch approve.
- **Sequences** — version-aware step builder (channel, delay, drafting
  prompt, stop rules, per-step error policy); publish = new active
  version; per-step stats (accept rate, reply rate) derived from touches.
- **Sourcing** — ICP filter editor (maps to Apollo params), run-sourcing
  button, CSV import (client-side parse `[Twenty]` + `import_batches`
  error reporting), triage list of researched leads to enroll.
- **Settings** — brand profile/voice, caps, confidence threshold,
  suppression list management, connection health (last Dux webhook seen,
  SendGrid status), pause switch.
- **Shell** — fixed 240px collapsible sidebar (28px items, active =
  transparent-light bg, count pills), brand switcher as a single dropdown
  with swappable inner pages `[Twenty MultiWorkspaceDropdown]`, `cmdk` ⌘K
  palette (Navigate / Recent leads / Actions) + `g`-prefixed go-to keys.

**Explicitly skipped from Twenty:** metadata-driven rendering, DB-persisted
page layouts, Jotai component-state framework, portal-per-cell editing,
advanced filter groups, favorites (deleted upstream anyway).

## 6. Guardrails

1. Per-brand daily caps (default ~20 LinkedIn connects / 25 messages /
   configurable emails per day) — beneath Dux Soup's own throttling.
2. Confidence threshold dial: 101 = review-everything mode; lower as trust
   builds.
3. Quiet hours + weekday-only sending, per brand timezone.
4. Per-brand pause switch freezing the tick.
5. New sequences start in review-everything mode for their first N sends.
6. `dry_run` brand flag: full pipeline runs but dispatch blocks log instead
   of send.
7. Suppression list enforced at dispatch time (email) + `do_not_contact`
   flag (LinkedIn).

## 7. Error handling

- Every external-call failure writes an `events` row (type `error`) on the
  affected lead; retryable failures retry up to 3× then `failed` + review
  queue; terminal provider errors fail fast without retry.
- Apollo 429/credit exhaustion: back off, surface in Settings health panel.
- Dux queue unreachable: touches stay `approved`; next tick retries.
- Webhook ingestion idempotent via `UNIQUE (channel, provider_message_id)`
  — Powabase webhooks have no replay protection, so the constraint is the
  dedupe.
- Staleness sweeper + retention: flag enrollments stuck > 1h; archive
  events older than a retention window if volume demands.
- Powabase `402 insufficient_credits`: do not retry; surface in UI.

## 8. Build order (each phase independently useful)

1. **Foundation** — schema (with §3.1 conventions) + RLS + GoTrue auth;
   tokens.css + shell; pipeline board, lead view, CSV import. (A working
   lite CRM.)
2. **Research** — researcher agent + `wf-research`; Studio tool keys.
3. **Apollo** — `wf-apollo-source` + `wf-enrich`; ICP editor.
4. **Sequences + email** — sequence versions, engine tick, copywriter,
   SendGrid send + event webhooks + suppressions. Email first: safest to
   test.
5. **LinkedIn** — Dux Soup dispatch + webhooks, tested against the
   founder's own profile before real leads.

## 9. Testing

- Schema/logic: SQL fixtures + PostgREST integration checks per phase.
- Workflows: dry-run mode against a **test brand** whose "leads" are the
  founder's own LinkedIn profile + email addresses.
- Webhook ingestion: replay captured Dux Soup / SendGrid payloads against
  armed workflows before deploying; replay twice to prove idempotency.
- Agents: golden-set of 5–10 known companies; eyeball research quality and
  draft voice before enabling auto-approve.

## 10. Open items (deliberately deferred)

- Attio one-way mirror (only if a team needs CRM-of-record later).
- AI-drafted reply suggestions after human takeover.
- gpt-trainer product-signal mining as a lead source.
- Sales Navigator-specific sourcing via Dux Soup visits.
- Notes/tasks polymorphic targets (Twenty's wide-nullable-FK pattern with a
  `num_nonnulls(...) = 1` CHECK) — add when notes need to attach to more
  than a person.
