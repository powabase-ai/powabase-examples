# Powabase Example Apps

Open-source reference applications built **on top of [Powabase](https://powabase.ai)**
— the AI BaaS — showing how to consume its platform primitives (agents, workflows,
knowledge bases, sources, GoTrue auth, per-project Postgres) to build real products.

Each app lives in its own subdirectory with its own README. What that subdirectory
contains differs on purpose: `rankforge/` ships a backend and a frontend, while
`powacrm/` deliberately ships no server at all.

## Apps

| App | What it is | Stack |
|---|---|---|
| [`rankforge/`](./rankforge) | Production SEO/GEO blog-article platform — multi-org, multi-brand: research → grounded generation → editorial review → publish, with autonomous content scouts. | FastAPI · psycopg3 · Next.js 16 · TanStack Query |
| [`powacrm/`](./powacrm) | Backend-less lite CRM for outbound sales — pipeline kanban, CSV import, and AI-researched company profiles with per-lead fit scores. No app server: the SPA talks straight to PostgREST under RLS, and the research worker is a `pg_cron` job inside Postgres that calls the researcher agent over HTTP. | React 19 · Vite · TanStack Query · PostgREST/RLS · pg_cron |

## Layout

```
powabase-examples/
  rankforge/        # a conventional app: its own API server in front of Powabase
    backend/        #   FastAPI
    frontend/       #   Next.js
    docs/
  powacrm/          # backend-less: the Powabase project IS the backend
    app/            #   React SPA -> PostgREST/RLS, the only code outside Powabase
    db/             #   migrations, RLS policies, RPCs, and the pg_cron research worker
    platform/       #   the researcher agent, defined as JSON and provisioned by script
    docs/
```

Each app is independently runnable — start from that app's `README.md`. Secrets live
in per-app `.env` files (gitignored); copy the provided `.env.example` to get going.
