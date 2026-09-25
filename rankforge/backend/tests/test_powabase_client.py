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


@respx.mock
async def test_retries_transient_503_then_succeeds():
    route = respx.get(f"{BASE}/api/agents").mock(
        side_effect=[
            httpx.Response(503, json={"error": "overloaded"}),
            httpx.Response(200, json={"agents": []}),
        ]
    )
    client = PowabaseClient(BASE, KEY, backoff_base=0)  # no real sleep
    try:
        assert await client.get_agents() == {"agents": []}
    finally:
        await client.aclose()
    assert route.call_count == 2  # retried the 503 once, then succeeded


@respx.mock
async def test_402_is_never_retried():
    route = respx.post(f"{BASE}/api/workflows/wf/execute").mock(
        return_value=httpx.Response(402, json={"error": "payment required"})
    )
    client = PowabaseClient(BASE, KEY, backoff_base=0)
    try:
        with pytest.raises(PowabaseError) as exc:
            await client.execute_workflow("wf", {})
    finally:
        await client.aclose()
    assert exc.value.status_code == 402
    assert route.call_count == 1  # a hard billing 402 must not retry


@respx.mock
async def test_gives_up_after_max_retries_on_persistent_503():
    route = respx.get(f"{BASE}/api/agents").mock(
        return_value=httpx.Response(503, json={"error": "overloaded"})
    )
    client = PowabaseClient(BASE, KEY, backoff_base=0, max_retries=2)
    try:
        with pytest.raises(PowabaseError) as exc:
            await client.get_agents()
    finally:
        await client.aclose()
    assert exc.value.status_code == 503
    assert route.call_count == 3  # initial + 2 retries


# --- review r1 I6: a stream with no `complete` event is marked incomplete ---
def _sse(*events):
    import json as _json

    return "".join(f"data: {_json.dumps(e)}\n\n" for e in events).encode()


@respx.mock
async def test_run_agent_collect_marks_cut_off_stream_incomplete():
    respx.post(f"{BASE}/api/agents/ag/run/stream").mock(
        return_value=httpx.Response(200, content=_sse(
            {"event": "start", "run_id": "r"},
            {"event": "content_delta", "delta": "# Half"},
        ))
    )
    client = PowabaseClient(BASE, KEY)
    try:
        res = await client.run_agent_collect("ag", "hi")
    finally:
        await client.aclose()
    assert res["incomplete"] is True and res["content"] == "# Half"


@respx.mock
async def test_run_agent_collect_complete_stream_is_not_incomplete():
    respx.post(f"{BASE}/api/agents/ag/run/stream").mock(
        return_value=httpx.Response(200, content=_sse(
            {"event": "content_delta", "delta": "# Whole"},
            {"event": "complete", "content": "# Whole", "run_id": "r"},
        ))
    )
    client = PowabaseClient(BASE, KEY)
    try:
        res = await client.run_agent_collect("ag", "hi")
    finally:
        await client.aclose()
    assert res["incomplete"] is False and res["content"] == "# Whole"


# --- review r2 minor: a `complete` event with status "failed" is an error ---
@respx.mock
async def test_run_agent_collect_failed_complete_is_an_error():
    respx.post(f"{BASE}/api/agents/ag/run/stream").mock(
        return_value=httpx.Response(200, content=_sse(
            {"event": "content_delta", "delta": "partial"},
            {"event": "complete", "content": "partial", "run_id": "r",
             "status": "failed", "error": "model overloaded"},
        ))
    )
    client = PowabaseClient(BASE, KEY)
    try:
        res = await client.run_agent_collect("ag", "hi")
    finally:
        await client.aclose()
    assert res["error"] and res["error"]["error"] == "model overloaded"
    assert res["incomplete"] is False


@respx.mock
async def test_run_agent_collect_completed_status_is_not_an_error():
    respx.post(f"{BASE}/api/agents/ag/run/stream").mock(
        return_value=httpx.Response(200, content=_sse(
            {"event": "complete", "content": "# Ok", "run_id": "r",
             "status": "completed", "error": None},
        ))
    )
    client = PowabaseClient(BASE, KEY)
    try:
        res = await client.run_agent_collect("ag", "hi")
    finally:
        await client.aclose()
    assert res["error"] is None and res["content"] == "# Ok"
