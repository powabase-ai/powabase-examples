"""Write the Powabase website's blog conventions onto ONE RankForge brand.

    uv run python scripts/seed_powabase_blog_profile.py --brand-id <uuid>

Targets the brand by id (names are only unique within an org). Idempotent:
overwrites blog_profile with the validated profile, sets url_pattern to the
trailing-slash blog URL if it's empty, and rolls back and exits non-zero unless
exactly one row was updated. Rules mirror website lib/blog.ts,
content/blog-categories.ts
and scripts/check-meta.ts (Sept 2026)."""

import argparse
import sys
from uuid import UUID

from psycopg.types.json import Json

from rankforge_backend.config import get_settings
from rankforge_backend.db import Database
from rankforge_backend.models.blog import BlogProfile

PROFILE = {
    "categories": [
        {"key": "rag", "label": "RAG & Retrieval", "technical": True,
         "description": "Indexing, retrieval, and knowledge bases on Postgres."},
        {"key": "agents", "label": "Agents & Workflows", "technical": True,
         "description": "Building, running, and automating agents with a database behind them."},
        {"key": "backend", "label": "Backend & Comparisons", "technical": True,
         "description": "Backend-as-a-service for AI apps, and how the options compare."},
        {"key": "coding-agents", "label": "Coding Agents", "technical": True,
         "description": "Claude Code, Codex, Cursor, MCP, and the backend they build on."},
        {"key": "models", "label": "Models & Cost", "technical": False,
         "description": "Running models, token efficiency, and keeping inference bills down."},
        {"key": "enterprise", "label": "Enterprise AI", "technical": False,
         "description": "Adoption, deployment, and buying decisions inside organisations."},
    ],
    "summary": {"enabled": True, "min_words": 40, "max_words": 60},
    "faq": {"enabled": True, "min": 3, "max": 6},
    "meta": {"title_max": 60, "description_max": 160},
    "links": {"min": 3, "max": 5, "trailing_slash": True, "hub_pages": [
        {"path": "/supabase-alternative/", "title": "Supabase alternative",
         "topics": ["supabase alternative", "alternative to supabase"]},
        {"path": "/firebase-alternative/", "title": "Firebase alternative",
         "topics": ["firebase alternative", "alternative to firebase"]},
        {"path": "/convex-alternative/", "title": "Convex alternative",
         "topics": ["convex alternative"]},
        {"path": "/neon-alternative/", "title": "Neon alternative",
         "topics": ["neon alternative", "serverless postgres"]},
        {"path": "/pinecone-alternative/", "title": "Pinecone alternative",
         "topics": ["pinecone alternative", "managed vector database"]},
        {"path": "/langchain-alternative/", "title": "LangChain alternative",
         "topics": ["langchain alternative", "rag framework"]},
        {"path": "/backend-as-a-service/", "title": "Backend as a service for AI apps",
         "topics": ["backend as a service", "baas"]},
        {"path": "/self-hosted-supabase/", "title": "Self-hosted Supabase",
         "topics": ["self-hosted supabase", "self-hosting supabase"]},
        {"path": "/vector-database/", "title": "Vector database on Postgres",
         "topics": ["vector database", "pgvector"]},
        {"path": "/free-mvp/", "title": "Free MVP build",
         "topics": ["free mvp", "mvp build"]},
    ]},
    "stance": "favor_brand",
}


class _NotExactlyOne(Exception):
    pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand-id", required=True, type=UUID)
    args = ap.parse_args(argv)
    # Fail fast on a typo, and store the validated dump (defaults filled in).
    profile = BlogProfile.model_validate(PROFILE).model_dump()
    db = Database(get_settings().powabase_database_url)
    db.open()
    try:
        # One transaction: raising inside it rolls the update back.
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "update public.business_profiles set blog_profile = %s, "
                "url_pattern = coalesce(nullif(url_pattern, ''), "
                "'https://powabase.ai/blog/{slug}/'), updated_at = now() "
                "where id = %s returning id, name, url_pattern",
                (Json(profile), args.brand_id),
            )
            rows = cur.fetchall()
            if len(rows) != 1:
                raise _NotExactlyOne(f"{len(rows)} brands matched {args.brand_id}")
        print(rows[0])
        return 0
    except _NotExactlyOne as e:
        print(f"aborted, nothing written: {e}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
