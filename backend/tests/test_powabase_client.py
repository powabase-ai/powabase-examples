"""PowabaseClient — verify the two-header contract and error handling.

Network is mocked with respx; no live Powabase project required.
"""

import httpx
import pytest
import respx

from rankforge_backend.powabase import PowabaseClient, PowabaseError

BASE = "https://ref.p.powabase.ai"
KEY = "service-role-secret"


def test_requires_base_url_and_key():
    with pytest.raises(ValueError):
        PowabaseClient("", KEY)
    with pytest.raises(ValueError):
        PowabaseClient(BASE, "")


@respx.mock
async def test_get_agents_sends_both_auth_headers():
    route = respx.get(f"{BASE}/api/agents").mock(
        return_value=httpx.Response(200, json={"agents": [], "total": 0})
    )
    client = PowabaseClient(BASE, KEY)
    try:
        result = await client.get_agents()
    finally:
        await client.aclose()

    assert result == {"agents": [], "total": 0}
    sent = route.calls.last.request
    # The #1 Powabase footgun: both headers, same key.
    assert sent.headers["apikey"] == KEY
    assert sent.headers["authorization"] == f"Bearer {KEY}"


@respx.mock
async def test_non_2xx_raises_powabase_error():
    respx.get(f"{BASE}/api/agents").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )
    client = PowabaseClient(BASE, KEY)
    try:
        with pytest.raises(PowabaseError) as exc:
            await client.get_agents()
    finally:
        await client.aclose()
    assert exc.value.status_code == 401
    assert exc.value.body == {"error": "unauthorized"}
