# platform/

Provisioning for PowaCRM's Powabase platform resources -- currently just the
`powacrm-researcher` agent. This directory is the *only* place these
resources get created; there is no dashboard step and no migration for them.

## What's here

- `researcher-agent.json` -- the agent definition (name, model, settings,
  system prompt, and the list of builtin tools it should have).
- `provision.sh` -- creates or updates the agent on your Powabase project and
  attaches its tools. **Idempotent**: it looks up the agent by `name` first,
  so re-running it updates the existing `powacrm-researcher` instead of
  creating a second one, and it checks each tool's current attachment before
  attaching it again.

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

## Two API gotchas this script works around

1. **A `tools` array passed to `POST /api/agents` (or `PATCH`) is silently
   dropped.** The call still returns 201/200 with an agent that has no
   tools attached and no error. Tools only attach via a separate call:
   `POST /api/agents/{id}/tools` with body
   `{"tool_type":"builtin","tool_name":"<name>"}`. `provision.sh` creates the
   agent without its `tools` field, then attaches each tool with a second
   request per tool.
2. **Workflow block ids must be UUIDs.** If a later workflow definition in
   this repo references block ids, use real UUIDs (e.g. `python3 -c "import
   uuid; print(uuid.uuid4())"`), not short human-readable strings -- the
   workflow API rejects (or silently mishandles) non-UUID block ids.

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
touches the database. The workflow that calls it (Task 5) is the only thing
that writes the research result back, via the `complete_research_job` RPC,
running as a normal role subject to RLS. Do not add `database_query`,
`database_write`, `http_request`, or `code_execute` to this agent's tool
list -- if a future task seems to need one of those on the researcher, that
is a sign the write belongs in the calling workflow instead.

`web_scrape` (Firecrawl) and `web_search` (Exa) run on Powabase platform
credits -- no external API keys are required for either.
