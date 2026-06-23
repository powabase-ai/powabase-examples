"""Does `source_ids`-scoped retrieval on one shared KB work well enough to avoid
per-article KBs? Index mixed-topic sources into ONE KB, then compare unscoped vs
scoped search: scoping must (a) restrict to the given sources and (b) still return
relevant, sufficient results.

Usage (from backend/): uv run python scripts/probe_kb_scoping.py
"""

import json
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


def pick_sources():
    with psycopg.connect(s.powabase_database_url) as conn:
        rows = conn.execute(
            "select rs.source_id, rr.topic from public.research_sources rs "
            "join public.research_runs rr on rr.id = rs.research_run_id "
            "where rs.status = 'extracted' order by rr.topic"
        ).fetchall()
    by_topic: dict[str, list[str]] = {}
    for sid, topic in rows:
        by_topic.setdefault(topic, []).append(sid)
    topics = [t for t, ids in by_topic.items() if ids]
    cms = next((t for t in topics if "cms" in t.lower()), None)
    baas = next((t for t in topics if "backend" in t.lower() or "baas" in t.lower()), None)
    return (cms, by_topic.get(cms, [])), (baas, by_topic.get(baas, []))


def src_label(r):
    for k in ("source_name", "source_id", "url", "name", "document_id"):
        if r.get(k):
            return f"{k}={str(r[k])[:40]}"
    return f"keys={list(r.keys())}"


def main() -> None:
    (cms_topic, cms_ids), (baas_topic, baas_ids) = pick_sources()
    cms_ids, baas_ids = cms_ids[:2], baas_ids[:2]
    print(f"CMS topic={cms_topic!r} sources={cms_ids}")
    print(f"BaaS topic={baas_topic!r} sources={baas_ids}")
    if not cms_ids or not baas_ids:
        print("need sources from two distinct topics; run more research first")
        return
    all_ids = cms_ids + baas_ids

    with httpx.Client(base_url=BASE, headers=H, timeout=120.0) as c:
        kb = c.post(
            "/api/knowledge-bases",
            json={"name": "__probe_scoping", "retrieval_config": {"method": "hybrid", "top_k": 5}},
        ).json()
        kb_id = kb.get("id") or kb.get("knowledge_base", {}).get("id")
        print("kb:", kb_id)
        for sid in all_ids:
            c.post(f"/api/knowledge-bases/{kb_id}/sources", json={"source_id": sid})
        for _ in range(60):
            items = c.get(f"/api/knowledge-bases/{kb_id}/sources").json().get("items", [])
            if items and all(i.get("index_status") in {"indexed", "failed", "cancelled"} for i in items):
                break
            time.sleep(2)

        query = "headless CMS pricing and content modeling"

        def run(label, body):
            res = c.post(f"/api/knowledge-bases/{kb_id}/search", json=body).json()
            results = res.get("results", [])
            print(f"\n=== {label}: {len(results)} results ===")
            if results:
                print("first-result keys:", list(results[0].keys()))
            for r in results:
                print(f"  score={r.get('score'):.3f}  {src_label(r)}  {r.get('text','')[:80]!r}")

        run("UNSCOPED", {"query": query, "top_k": 5})
        run("SCOPED to CMS sources", {"query": query, "top_k": 5, "source_ids": cms_ids})
        run("SCOPED to BaaS sources (should be off-topic)", {"query": query, "top_k": 5, "source_ids": baas_ids})

        print("\nfull first scoped result:")
        scoped = c.post(
            f"/api/knowledge-bases/{kb_id}/search",
            json={"query": query, "top_k": 1, "source_ids": cms_ids},
        ).json().get("results", [])
        if scoped:
            print(json.dumps(scoped[0], indent=2)[:600])

        c.delete(f"/api/knowledge-bases/{kb_id}")
        print("\ndeleted kb")


if __name__ == "__main__":
    main()
