"""Write the Powabase website's blog conventions onto a RankForge brand.

    uv run python scripts/seed_powabase_blog_profile.py --brand-name Powabase

Idempotent: overwrites blog_profile only; sets url_pattern to the trailing-slash
blog URL if it's empty. Rules mirror website lib/blog.ts, content/blog-categories.ts
and scripts/check-meta.ts (Sept 2026)."""

import argparse

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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand-name", required=True)
    args = ap.parse_args()
    BlogProfile.model_validate(PROFILE)  # fail fast on a typo
    db = Database(get_settings().powabase_database_url)
    db.open()
    try:
        row = db.fetch_one(
            "update public.business_profiles set blog_profile = %s, "
            "url_pattern = coalesce(nullif(url_pattern, ''), "
            "'https://powabase.ai/blog/{slug}/'), updated_at = now() "
            "where lower(name) = lower(%s) returning id, name, url_pattern",
            (Json(PROFILE), args.brand_name),
        )
        print(row or f"no brand named {args.brand_name!r}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
