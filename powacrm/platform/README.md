# platform/

Provisioning for PowaCRM's one Powabase platform resource: the
`powacrm-researcher` agent. This directory is the *only* place it gets
created; there is no dashboard step and no migration for it.

**This app does not use Powabase workflows.** It used to -- a scheduled
workflow (`wf-research-tick`) drove the research worker in an earlier
revision of this phase -- and that workflow has been deleted. The worker now
lives inside the database as a `pg_cron` job calling `run_research_tick()`
(see `db/migrations/0013_inline_worker.sql` and the root `README.md`'s
Research section). The reason is below, and it is the reason this directory
will not grow a workflow definition back.

## What's here

- `researcher-agent.json` -- the agent definition (name, model, settings,
  system prompt, and the list of builtin tools it should have).
- `provision.sh` -- creates or updates the agent from that file and
  reconciles its attached tools to exactly what it lists, adding anything
  missing and removing anything the live agent has that isn't declared.
  **Idempotent**: it looks up the agent by `name` first, so re-running
  updates it instead of creating a second copy, and it checks each tool's
  current attachment before attaching it again.

## Running it

```bash
export VITE_POWABASE_URL='https://<your-project-ref>.p.powabase.ai'
export PB_SERVICE_KEY='<service role key>'
./platform/provision.sh
```

`PB_SERVICE_KEY` is a service-role key with full platform access -- it must
never be committed, put in `app/`, or run from anywhere other than a trusted
machine (your workstation or CI secrets, not a client-side build). Both
variables are read from the environment only; nothing in this directory
hardcodes a URL or a key.

## Why this app does not use Powabase workflows

This is the single most expensive thing rediscovered in this project, so it
is written down in full -- and it is exactly why the research worker is a
`pg_cron` job in the database instead of a scheduled workflow.

**A workflow `agent` block runs the agent with no tools and no system
prompt.** `POST /api/agents/{id}/run/stream` executes the agent's ReAct tool
loop. **`POST /api/agents/{id}/run` -- the non-streaming path, which the
workflow `agent` block uses internally -- does not.** An agent invoked that
way arrives with no tools *and* no system prompt, and simply answers from
model recall. Nothing about this is reported: the block returns
`status: success`, a plausible answer, and no warning.

How it was established on a live project:

| probe | result |
| --- | --- |
| One `agent` block pointed at `powacrm-researcher`, asked *"use web_scrape on example.com; if you have no web_scrape tool reply exactly NO_TOOLS_AVAILABLE"* | **`NO_TOOLS_AVAILABLE`**, 615 prompt tokens |
| The same message via `/run/stream` | *"I do have a web_scrape tool available."*, 2046 prompt tokens |
| A real research job through the `agent` block | 13.4 s, one LLM turn, `tech_stack: []`, and a `sources` array listing a URL it never fetched |
| The same prompt via `/run/stream` | 44 s, four tool calls, grounded output |
| `POST /run` directly, no workflow involved | also `NO_TOOLS_AVAILABLE` -- the seam is streaming vs non-streaming, not workflows |
| Block configs meant to re-attach tools: `"tools"` as strings, as `{tool_type,tool_name}` objects, and `"use_agent_tools": true` | all three still 615 prompt tokens and `NO_TOOLS_AVAILABLE` -- **no block config fixes this** |

For a research agent this is not a degraded result, it is a dangerous one.
The model keeps emitting a confident `sources` array whether or not it
fetched anything, so the ungrounded version writes **fabricated citations**
into `companies.research_data`, which the app then shows a seller as
observed fact. In outbound that is worse than having no research at all.

That defect alone would have been enough, but it was not the only one found
while the worker was still a workflow:

- A `condition` block **gates nothing** on its own; the routing lives on the
  edge's `condition` field, and without it both branches run.
- Blocks execute **strictly sequentially**, so parallel branches bought no
  throughput.
- A block `id` that is not a UUID returns an opaque **HTTP 500**, not a
  validation error.
- An unresolvable `agent_id` **silently degrades** to a default model with no
  system prompt rather than failing.

None of that is a knock on the *agent* -- calling `powacrm-researcher`
directly over `/run/stream` produces genuinely grounded research, and that
call is exactly what `run_research_tick()` makes from inside Postgres via
the `http` extension, scheduled by `pg_cron` every minute. It was the
workflow graph as a place to put logic that proved unsound. **If you copy one
thing from this example, copy that: any workflow whose `agent` block depends
on tools -- `web_search`, `knowledge_search`, a custom tool, an MCP tool -- is
silently not using them.**

## API gotchas this script works around

1. **A `tools` array passed to `POST /api/agents` (or `PATCH`) is silently
   dropped.** The call still returns 201/200 with an agent that has no
   tools attached and no error. Tools only attach via a separate call:
   `POST /api/agents/{id}/tools` with body
   `{"tool_type":"builtin","tool_name":"<name>"}`. `provision.sh` creates the
   agent without its `tools` field, then attaches each tool with a second
   request per tool.
2. **Pagination.** `GET /api/agents` paginates (`limit`/`offset`, `total` in
   the response), so `provision.sh` walks pages when looking the agent up by
   name rather than assuming it is on the first one.

## Security note: why this agent has only `web_scrape` and `web_search`

The researcher agent's whole job is reading attacker-controlled text -- a
prospect's public website, editable by anyone who has access to it -- and
turning it into a JSON research report. Powabase agent database tools
(`database_query`, `database_write`) run on the platform's superuser
connection with Row Level Security bypassed, and tools like `http_request`
/ `code_execute` give an agent an even broader blast radius. An agent that
holds any of those while reading untrusted pages is one prompt injection
away from writing anywhere in the schema or reaching arbitrary hosts, and no
RLS policy would stop it, because RLS does not apply to that connection.

So `powacrm-researcher` gets exactly two tools -- `web_scrape` and
`web_search` -- and nothing else. It reads and returns JSON; it never
touches the database. `run_research_tick()`, the `SECURITY DEFINER` function
that calls this agent from `pg_cron`, is the only thing that writes the
research result back, via the `complete_research_job` RPC. Do not add
`database_query`, `database_write`, `http_request`, or `code_execute` to
this agent's tool list -- if a future task seems to need one of those on the
researcher, that is a sign the write belongs in the caller instead.

`web_scrape` (Firecrawl) and `web_search` (Exa) run on Powabase platform
credits -- no external API keys are required for either.
