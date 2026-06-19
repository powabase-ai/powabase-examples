"""Probe POST /api/sources/import-url end-to-end: import a URL as a Source, poll
extraction, then fetch the markdown derivative. Learns the live request/response shape.

Usage (from backend/): uv run python scripts/probe_import_url.py
"""

import time

import httpx

from rankforge_backend.config import get_settings

s = get_settings()
BASE = s.powabase_base_url.rstrip("/")
KEY = s.powabase_service_role_key
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
TERMINAL = {"extracted", "attention_required", "failed", "cancelled"}
URL = "https://supabase.com/"


def main() -> None:
    with httpx.Client(base_url=BASE, headers=H, timeout=120.0) as c:
        r = c.post(
            "/api/sources/import-url", json={"mode": "urls", "urls": [URL]}
        )
        print("import-url:", r.status_code)
        print("body:", r.text[:600])
        if r.status_code not in (200, 201, 409):
            return
        body = r.json()
        # response shape unknown — try common shapes
        sid = None
        if isinstance(body, dict):
            sid = body.get("id") or (body.get("duplicate") or {}).get("id")
            if not sid and isinstance(body.get("sources"), list) and body["sources"]:
                sid = body["sources"][0].get("id")
        elif isinstance(body, list) and body:
            sid = body[0].get("id")
        print("source id:", sid)
        if not sid:
            return

        for _ in range(60):
            src = c.get(f"/api/sources/{sid}").json()
            st = src.get("extraction_status")
            print("  status:", st)
            if st in TERMINAL:
                break
            time.sleep(2)

        md = c.get(f"/api/sources/{sid}/derivatives/markdown/download")
        print("markdown derivative:", md.status_code, "len=", len(md.text))
        print("--- first 400 chars ---")
        print(md.text[:400])


if __name__ == "__main__":
    main()
