# Design 10 — Editorial workflow + multi-brand model

Status: **agreed** (2026-06-18). PRD Phases 7 + §3 (brands).

## Lifecycle & library

- Status: `draft → in_review → approved → published → archived`.
- **Versioning** — snapshot `article_versions` on every save; diff + restore.
- **Library** — search/filter by status, keyword, author, score, brand, date.
- **Comments** — lightweight per-article review notes in v1 (writers ↔ editors).

## Roles & permissions

- Roles: writer / editor / admin (on `profiles`).
- **Publish = editors + admins.** Writers draft and submit for review (`in_review`);
  editors/admins approve + publish. Admins also manage team, integrations, KB, templates.

## Multi-brand

- **Brand switcher** scopes the whole UI by `business_id` (soft scoping, not hard tenancy).
- Everything content-bearing carries `business_id`: research_runs, briefs, articles,
  scouts, opportunities, site_pages, media_assets, pseo_campaigns.
- Drafts are **team-visible** by default; author/owner tracked.
- Per-brand config: niche, competitors, brand KB, image library, scouts, sitemap.

## Decisions

- Fact-check/reflection: see Design 09.
- Comments **in v1** (lightweight).
- Publish restricted to **editors + admins**.
- Drafts **team-visible** (no per-user privacy in v1).

## Open / deferred

- RLS policy specifics per role (backend enforces authz; RLS is defense-in-depth).
- Notifications/assignments (who's reviewing) — later.
