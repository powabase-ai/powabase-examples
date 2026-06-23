"""Probe the grounding KB flow: create KB -> index an existing research Source ->
search. Learns the live shapes before building the grounding service.

Usage (from backend/): uv run python scripts/probe_kb.py
"""

import time

import httpx
import psycopg

from rankforge_backend.config import get_settings

s = get_settings()
BASE = s.powabase_base_url.rstrip("/")
H = {
    "apikey": s.powabase_service_role_key,
    "Authorization": f"Bearer {s.powabase_service_role_key}",
    "Content-Type": "application/json",
}


def main() -> None:
    with psycopg.connect(s.powabase_database_url) as conn:
        row = conn.execute(
            "select source_id, title from public.research_sources "
            "where status = 'extracted' limit 1"
        ).fetchone()
    if not row:
        print("no extracted research sources to index")
        return
    source_id, title = row
    print("using source:", source_id, "—", title)

    with httpx.Client(base_url=BASE, headers=H, timeout=120.0) as c:
        kb = c.post(
            "/api/knowledge-bases",
            json={
                "name": "__probe_kb",
                "retrieval_config": {"method": "hybrid", "top_k": 5},
            },
        ).json()
        kb_id = kb.get("id") or kb.get("knowledge_base", {}).get("id")
        print("kb:", kb_id)

        add = c.post(f"/api/knowledge-bases/{kb_id}/sources", json={"source_id": source_id})
        print("add source:", add.status_code, add.text[:160])

        for _ in range(40):
            items = c.get(f"/api/knowledge-bases/{kb_id}/sources").json()
            st = (items.get("items") or [{}])[0].get("index_status")
            print("  index_status:", st)
            if st in {"indexed", "failed", "cancelled"}:
                break
            time.sleep(2)

        res = c.post(
            f"/api/knowledge-bases/{kb_id}/search",
            json={"query": "what is a backend as a service", "top_k": 3},
        ).json()
        results = res.get("results", [])
        print(f"--- search: {len(results)} results ---")
        for r in results:
            print(f"  score={r.get('score'):.3f}  {r.get('text', '')[:120]!r}")

        d = c.delete(f"/api/knowledge-bases/{kb_id}")
        print("deleted kb:", d.status_code)


if __name__ == "__main__":
    main()
