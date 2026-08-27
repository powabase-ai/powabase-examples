# Powabase Example Apps

Open-source reference applications built **on top of [Powabase](https://powabase.ai)**
— the AI BaaS — showing how to consume its platform primitives (agents, workflows,
knowledge bases, sources, GoTrue auth, per-project Postgres) to build real products.

Each app lives in its own subdirectory with its own README, backend, and frontend.

## Apps

| App | What it is | Stack |
|---|---|---|
| [`rankforge/`](./rankforge) | Production SEO/GEO blog-article platform — multi-org, multi-brand: research → grounded generation → editorial review → publish, with autonomous content scouts. | FastAPI · psycopg3 · Next.js 16 · TanStack Query |
| [`powacrm/`](./powacrm) | Backend-less outbound sales automation — lite CRM + AI-researched, hyperpersonalized LinkedIn/email sequences (Apollo · Dux Soup · SendGrid) run entirely by Powabase workflows and agents. | React 19 · Vite · TanStack Query · PostgREST/RLS |

## Layout

```
powabase-examples/
  rankforge/        # one self-contained app (see rankforge/README.md to run it)
    backend/
    frontend/
    docs/
```

Each app is independently runnable — start from that app's `README.md`. Secrets live
in per-app `.env` files (gitignored); copy the provided `.env.example` to get going.
