"""Shared agent provisioning.

Each RankForge agent is defined once in code (name, model, system prompt, tools).
`ensure_agent` looks it up by name, creating it the first time and **PATCHing its
system_prompt/settings when it already exists** — so editing a prompt in code takes
effect on the next run, without deleting the live agent. Ids are cached per process
to avoid a lookup on every call.
"""

import asyncio
from collections.abc import Sequence
from typing import Any

from ..powabase import PowabaseClient

_cache: dict[str, str] = {}
_lock = asyncio.Lock()


async def ensure_agent(
    client: PowabaseClient,
    *,
    name: str,
    model: str,
    system_prompt: str,
    settings: dict[str, Any] | None = None,
    builtin_tools: Sequence[str] = (),
) -> str:
    if name in _cache:
        return _cache[name]
    # Serialize the cold-start miss path so two concurrent first-calls for the same
    # agent can't both create it (duplicate-agent leak).
    async with _lock:
        if name in _cache:  # double-check after acquiring the lock
            return _cache[name]
        return await _provision(
            client,
            name=name,
            model=model,
            system_prompt=system_prompt,
            settings=settings,
            builtin_tools=builtin_tools,
        )


async def _provision(
    client: PowabaseClient,
    *,
    name: str,
    model: str,
    system_prompt: str,
    settings: dict[str, Any] | None,
    builtin_tools: Sequence[str],
) -> str:
    listing = await client.get_agents()
    existing = next(
        (a for a in listing.get("agents", []) if a.get("name") == name), None
    )
    if existing:
        agent_id = existing["id"]
        # Code is the source of truth — refresh prompt/settings if they've drifted.
        try:
            await client.update_agent(
                agent_id, model=model, system_prompt=system_prompt, settings=settings
            )
        except Exception:  # noqa: BLE001 — a failed refresh shouldn't block the run
            pass
        _cache[name] = agent_id
        return agent_id
    created = await client.create_agent(
        name=name, model=model, system_prompt=system_prompt, settings=settings
    )
    agent_id = created.get("id") or created.get("agent", {}).get("id")
    for tool in builtin_tools:
        await client.attach_builtin_tool(agent_id, tool)
    _cache[name] = agent_id
    return agent_id
