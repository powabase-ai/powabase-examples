# RankForge frontend

Next.js 16 (App Router) editorial UI. Talks to the FastAPI backend; uses the
Powabase **Anon** key only (for GoTrue sign-in / RLS-respecting reads). No secrets.

## Dev

```bash
cp .env.example .env
npm install
npm run dev        # → http://localhost:3000
```

> This is a lean scaffold (landing page + backend health check). Real UI is built
> per the PRD phases. We'll add shadcn/ui + TanStack Query providers as M1 starts.
