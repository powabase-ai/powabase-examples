"""Probe whether web_search works on this project WITHOUT a per-project EXA_API_KEY
(i.e. via platform credits / AI-on-us) or whether it errors 'Exa API key not configured'.

Provisions a throwaway agent + web_search tool, runs one streaming query, prints the
SSE, then deletes the agent. Usage (from backend/): uv run python scripts/probe_web_search.py
"""

import httpx

from rankforge_backend.config import get_settings

s = get_settings()
BASE = s.powabase_base_url.rstrip("/")
KEY = s.powabase_service_role_key
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}


def main() -> None:
    with httpx.Client(base_url=BASE, headers=H, timeout=90.0) as c:
        agent = c.post(
            "/api/agents",
            json={
                "name": "__probe_web_search",
                "model": "claude-sonnet-4-6",
                "system_prompt": "You are a research assistant. Always use the web_search tool to answer.",
                "settings": {"temperature": 0},
            },
        ).json()
        aid = agent.get("id") or agent.get("agent", {}).get("id")
        print("agent:", aid)

        t = c.post(
            f"/api/agents/{aid}/tools",
            json={"tool_type": "builtin", "tool_name": "web_search"},
        )
        print("attach web_search:", t.status_code, t.text[:200])
        # confirm it's actually attached
        lst = c.get(f"/api/agents/{aid}/tools")
        print("agent tools:", lst.status_code, lst.text[:300])

        print("--- streaming run ---")
        verdict = "unknown"
        try:
            with c.stream(
                "POST",
                f"/api/agents/{aid}/run/stream",
                json={
                    "message": "Use web_search to find one recent article about 'generative engine optimization'. Give its title and URL."
                },
            ) as resp:
                print("stream status:", resp.status_code)
                n = 0
                for line in resp.iter_lines():
                    if not line:
                        continue
                    print(line[:300])
                    low = line.lower()
                    if "exa api key not configured" in low or "not configured" in low:
                        verdict = "NEEDS_KEY"
                    if "insufficient_credits" in low:
                        verdict = "OUT_OF_CREDITS"
                    if '"tool"' in low and "web_search" in low and "result" in low:
                        verdict = "WORKS_VIA_PLATFORM"
                    n += 1
                    if n > 160:
                        print("...(truncated)")
                        break
        finally:
            d = c.delete(f"/api/agents/{aid}")
            print("deleted agent:", d.status_code)
        print("VERDICT:", verdict)


if __name__ == "__main__":
    main()
