# RankForge backend

FastAPI service that powers RankForge: research, briefs, generation, scoring, and
publishing. Consumes a Powabase project — direct Postgres for app data, `/api/*`
for agents/workflows/KBs. See [`../docs/architecture.md`](../docs/architecture.md).

## Dev

```bash
cp .env.example .env        # fill in POWABASE_* from the Connect modal
uv sync                     # install deps (incl. dev)
uv run uvicorn rankforge_backend.main:app --reload --port 8000
# → http://localhost:8000/docs  (OpenAPI)  ·  /health  ·  /health/ready
```

## Tests

Hermetic — no DB, no network. Mock at the `Database` / `PowabaseClient` boundary.

```bash
uv run pytest          # never bare `pytest`
```

## Layout

```
src/rankforge_backend/
├── main.py            # FastAPI app + lifespan (pool + Powabase client on app.state)
├── config.py          # Pydantic v2 settings from env
├── db.py              # psycopg3 pooled Database() — app data (public.*) + ai.* reads
├── powabase/client.py # async /api/* client (agents, workflows, KBs)
└── routes/            # API routers (health today; research/briefs/articles/... next)
schema/0001_init.sql   # app tables (apply to the project's Database URL)
```

## Adding a migration

For now, app schema is raw SQL in `schema/`. Add `schema/000N_*.sql` and apply the
whole set (in order, idempotently, tracked in `public.schema_migrations`) with
`uv run python scripts/apply_schema.py` — not a single `psql -f`, which would leave a
fresh DB missing every other migration. We may move to Alembic once it stabilizes.

