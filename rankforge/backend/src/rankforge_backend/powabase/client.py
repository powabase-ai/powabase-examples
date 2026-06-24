"""Async client for the Powabase `/api/*` surface.

Scope: only the endpoints RankForge needs — agents (research), workflows
(generation pipeline), and knowledge bases / sources (brand grounding). For app
data, use direct Postgres (`db.py`) instead.

Conventions baked in (see the `powabase` skill for the why):
- Two headers on every call: `apikey` AND `Authorization: Bearer <key>` — sending
  one is the #1 cause of 401s.
- Service Role (Secret) key, server-side only.
- The `/api/*` surface is still evolving; verify exact request/response shapes
  against https://docs.powabase.ai before trusting a field. Endpoints below are
  the canonical flow from the skill, kept thin on purpose.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx


class PowabaseError(RuntimeError):
    """Raised for non-2xx responses from the Powabase API."""

    def __init__(self, status_code: int, body: Any):
        self.status_code = status_code
        self.body = body
        super().__init__(f"Powabase API error {status_code}: {body}")


class PowabaseClient:
    """Thin async wrapper over the Powabase REST API.

    Lifecycle: create once, reuse the underlying httpx client, `await aclose()` on
    shutdown.
    """

    def __init__(self, base_url: str, service_role_key: str, *, timeout: float = 60.0):
        if not base_url or not service_role_key:
            raise ValueError("base_url and service_role_key are required")
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Content-Type": "application/json",
        }
        self._client = httpx.AsyncClient(
            base_url=self._base_url, headers=self._headers, timeout=timeout
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- low-level ---
    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = await self._client.request(method, path, **kwargs)
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            raise PowabaseError(resp.status_code, body)
        if resp.content:
            return resp.json()
        return None

    # --- agents (research) ---
    async def get_agents(self) -> Any:
        """Verify connectivity / list agents. Good for a health check."""
        return await self._request("GET", "/api/agents")

    async def create_agent(
        self,
        *,
        name: str,
        model: str,
        system_prompt: str,
        settings: dict[str, Any] | None = None,
    ) -> Any:
        """Create an agent. Note: tuning fields (temperature etc.) must nest in
        `settings` — top-level is silently dropped."""
        body: dict[str, Any] = {
            "name": name,
            "model": model,
            "system_prompt": system_prompt,
        }
        if settings:
            body["settings"] = settings
        return await self._request("POST", "/api/agents", json=body)

    async def update_agent(
        self,
        agent_id: str,
        *,
        model: str | None = None,
        system_prompt: str | None = None,
        settings: dict[str, Any] | None = None,
    ) -> Any:
        """Update an existing agent (PATCH /api/agents/{id}).

        The update body honors only name/model/system_prompt/settings (same as
        create); tuning fields must nest in `settings`. We use this to keep the
        code the source of truth for an agent's prompt/settings on every boot."""
        body: dict[str, Any] = {}
        if model is not None:
            body["model"] = model
        if system_prompt is not None:
            body["system_prompt"] = system_prompt
        if settings is not None:
            body["settings"] = settings
        return await self._request("PATCH", f"/api/agents/{agent_id}", json=body)

    async def attach_builtin_tool(self, agent_id: str, tool_name: str) -> Any:
        """Attach a builtin tool. The live API requires BOTH tool_type and
        tool_name (tool_name alone → 400)."""
        return await self._request(
            "POST",
            f"/api/agents/{agent_id}/tools",
            json={"tool_type": "builtin", "tool_name": tool_name},
        )

    async def delete_agent(self, agent_id: str) -> Any:
        return await self._request("DELETE", f"/api/agents/{agent_id}")

    async def run_agent_collect(
        self, agent_id: str, message: str, *, session_id: str | None = None
    ) -> dict[str, Any]:
        """Run an agent via /run/stream and collect the result.

        Consumes the SSE stream and returns the final assembled content plus tool
        activity and ids. Use this (not /run) whenever the agent has tools.
        """
        result: dict[str, Any] = {
            "content": "",
            "run_id": None,
            "session_id": session_id,
            "tool_results": [],
            "error": None,
        }
        parts: list[str] = []
        async for line in self.run_agent_stream(
            agent_id, message, session_id=session_id
        ):
            if not line.startswith("data:"):
                continue
            try:
                evt = json.loads(line[len("data:") :].strip())
            except json.JSONDecodeError:
                continue
            kind = evt.get("event")
            if kind == "start":
                result["run_id"] = evt.get("run_id")
                result["session_id"] = evt.get("session_id")
            elif kind == "content_delta":
                parts.append(evt.get("delta", ""))
            elif kind == "tool_result":
                result["tool_results"].append(
                    {
                        "tool": evt.get("tool_name"),
                        "preview": evt.get("result_preview"),
                    }
                )
            elif kind == "complete":
                if evt.get("content"):
                    result["content"] = evt["content"]
                result["run_id"] = evt.get("run_id", result["run_id"])
                result["session_id"] = evt.get("session_id", result["session_id"])
            elif kind == "error":
                result["error"] = evt
        if not result["content"]:
            result["content"] = "".join(parts)
        return result

    async def run_agent_stream(
        self, agent_id: str, message: str, *, session_id: str | None = None
    ) -> AsyncIterator[str]:
        """Stream an agent run (the tool-bearing path — required for web tools/KB).

        Yields raw SSE lines; parse with an SSE reader at the call site.
        `/run` (non-stream) has no tools and no ReAct loop — don't use it for
        research.
        """
        payload: dict[str, Any] = {"message": message}
        if session_id:
            payload["session_id"] = session_id
        async with self._client.stream(
            "POST", f"/api/agents/{agent_id}/run/stream", json=payload
        ) as resp:
            if resp.status_code >= 400:
                body = await resp.aread()
                raise PowabaseError(resp.status_code, body.decode(errors="replace"))
            async for line in resp.aiter_lines():
                if line:
                    yield line

    async def run_agent(
        self, agent_id: str, message: str, *, session_id: str | None = None
    ) -> Any:
        """Synchronous run — no tools, no ReAct loop. Use for plain LLM tasks
        (e.g. brief generation). For any tool use, use run_agent_stream/collect."""
        body: dict[str, Any] = {"message": message}
        if session_id:
            body["session_id"] = session_id
        return await self._request("POST", f"/api/agents/{agent_id}/run", json=body)

    async def get_run(self, run_id: str) -> Any:
        """Highest-signal way to debug a finished/failed run."""
        return await self._request("GET", f"/api/agents/runs/{run_id}")

    # --- workflows (generation pipeline) ---
    async def execute_workflow(self, workflow_id: str, inputs: dict[str, Any]) -> Any:
        """Kick off the generation workflow. Rate-limited 20/min/user → 429."""
        return await self._request(
            "POST", f"/api/workflows/{workflow_id}/execute", json={"inputs": inputs}
        )

    # --- knowledge bases / sources (brand grounding) ---
    async def upload_source(self, file_name: str, content: bytes, mime: str) -> Any:
        """Upload a source file (multipart). Poll get_source() until `extracted`."""
        files = {"file": (file_name, content, mime)}
        # multipart: drop the JSON content-type for this call
        headers = {k: v for k, v in self._headers.items() if k != "Content-Type"}
        resp = await self._client.post(
            "/api/sources/upload", files=files, headers=headers
        )
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            raise PowabaseError(resp.status_code, body)
        return resp.json()

    async def get_source(self, source_id: str) -> Any:
        return await self._request("GET", f"/api/sources/{source_id}")

    async def delete_source(self, source_id: str) -> Any:
        """Delete a Source project-wide. Use after removing it from any KB."""
        return await self._request("DELETE", f"/api/sources/{source_id}")

    async def import_url(self, url: str) -> Any:
        """Import a web page as a Source (Firecrawl-backed). Returns
        {count, sources:[{id, name, url}]}. Project-wide dedup: re-importing the
        same URL reuses the existing source."""
        return await self._request(
            "POST", "/api/sources/import-url", json={"mode": "urls", "urls": [url]}
        )

    async def get_source_markdown(self, source_id: str) -> str:
        """Fetch a source's extracted markdown derivative (raw text, not JSON)."""
        resp = await self._client.get(
            f"/api/sources/{source_id}/derivatives/markdown/download"
        )
        if resp.status_code >= 400:
            raise PowabaseError(resp.status_code, resp.text)
        return resp.text

    async def create_kb(
        self,
        name: str,
        *,
        description: str | None = None,
        retrieval_config: dict[str, Any] | None = None,
    ) -> Any:
        body: dict[str, Any] = {"name": name}
        if description:
            body["description"] = description
        if retrieval_config:
            body["retrieval_config"] = retrieval_config
        return await self._request("POST", "/api/knowledge-bases", json=body)

    async def add_source_to_kb(self, kb_id: str, source_id: str) -> Any:
        """Triggers indexing (idempotent re-index). 400s unless `extracted`."""
        return await self._request(
            "POST",
            f"/api/knowledge-bases/{kb_id}/sources",
            json={"source_id": source_id},
        )

    async def remove_source_from_kb(self, kb_id: str, indexed_source_id: str) -> Any:
        """De-index a source from a KB. The path id is the KB's INDEXED-source id
        (from list_kb_sources), which can differ from the raw source_id — resolve
        it first. Leaves the Source itself intact — call delete_source() after to
        remove it fully."""
        return await self._request(
            "DELETE", f"/api/knowledge-bases/{kb_id}/sources/{indexed_source_id}"
        )

    async def list_kb_sources(self, kb_id: str) -> Any:
        """Indexed sources + their index_status (pending/indexing/indexed/failed)."""
        return await self._request("GET", f"/api/knowledge-bases/{kb_id}/sources")

    async def search_kb(
        self,
        kb_id: str,
        query: str,
        *,
        top_k: int = 12,
        source_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Search the KB → list of {chunk_id, score, text, source_id, meta}.

        `source_ids` restricts retrieval to those sources (verified to filter the
        candidate set, not post-filter) — used to scope an article to its own
        research sources within one shared brand KB.
        """
        body: dict[str, Any] = {"query": query, "top_k": top_k}
        if source_ids:
            body["source_ids"] = source_ids
        resp = await self._request(
            "POST", f"/api/knowledge-bases/{kb_id}/search", json=body
        )
        return resp.get("results", []) if isinstance(resp, dict) else []

    async def update_kb(
        self,
        kb_id: str,
        *,
        retrieval_config: dict[str, Any] | None = None,
        name: str | None = None,
        description: str | None = None,
    ) -> Any:
        """PATCH a KB. retrieval_config (reranker/method/top_k/query_enrichment) is
        query-time and takes effect on the next search with no reindex."""
        body: dict[str, Any] = {}
        if retrieval_config is not None:
            body["retrieval_config"] = retrieval_config
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        return await self._request(
            "PATCH", f"/api/knowledge-bases/{kb_id}", json=body
        )

    async def delete_kb(self, kb_id: str) -> Any:
        return await self._request("DELETE", f"/api/knowledge-bases/{kb_id}")
