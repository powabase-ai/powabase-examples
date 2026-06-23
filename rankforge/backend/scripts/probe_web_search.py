"""Probe whether web_search (Exa) and web_scrape (Firecrawl) work on this project.

Provisions a throwaway agent with both tools, runs one streaming query that forces a
search + a scrape, prints the SSE, then deletes the agent.
Usage (from backend/): uv run python scripts/probe_web_search.py
"""

import httpx

from rankforge_backend.config import get_settings

s = get_settings()
BASE = s.powabase_base_url.rstrip("/")
KEY = s.powabase_service_role_key
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}


def main() -> None:
    with httpx.Client(base_url=BASE, headers=H, timeout=120.0) as c:
        agent = c.post(
            "/api/agents",
            json={
                "name": "__probe_web_tools",
                "model": "claude-sonnet-4-6",
                "system_prompt": "You are a research assistant. Use web_search and web_scrape to answer.",
                "settings": {"temperature": 0},
            },
        ).json()
        aid = agent.get("id") or agent.get("agent", {}).get("id")
        print("agent:", aid)
        for tool in ("web_search", "web_scrape"):
            r = c.post(
                f"/api/agents/{aid}/tools",
                json={"tool_type": "builtin", "tool_name": tool},
            )
            print(f"attach {tool}:", r.status_code)

        print("--- streaming run ---")
        search_ok = scrape_ok = False
        needs_key = False
        try:
            with c.stream(
                "POST",
                f"/api/agents/{aid}/run/stream",
                json={
                    "message": "Use web_search to find the homepage URL for 'Firecrawl', then use web_scrape on that URL. Report the page's main heading and one sentence about it."
                },
            ) as resp:
                print("stream status:", resp.status_code)
                n = 0
                for line in resp.iter_lines():
                    if not line:
                        continue
                    low = line.lower()
                    if '"event": "tool_result"' in low or "tool_result" in low:
                        print(line[:400])
                        if "not configured" in low:
                            needs_key = True
                        if "web_search" in low and "not configured" not in low:
                            search_ok = True
                        if "web_scrape" in low and "not configured" not in low:
                            scrape_ok = True
                    elif '"event": "tool_call"' in low:
                        print(line[:300])
                    n += 1
                    if n > 220:
                        print("...(truncated)")
                        break
        finally:
            c.delete(f"/api/agents/{aid}")
            print("deleted agent")
        print(
            f"VERDICT: web_search={'OK' if search_ok else 'FAIL'} "
            f"web_scrape={'OK' if scrape_ok else 'FAIL'} "
            f"needs_key={needs_key}"
        )


if __name__ == "__main__":
    main()
