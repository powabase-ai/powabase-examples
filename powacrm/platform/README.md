# platform/

Provisioning for PowaCRM's Powabase platform resources: the
`powacrm-researcher` agent and the `wf-research-tick` scheduled worker that
drives it. This directory is the *only* place these resources get created;
there is no dashboard step and no migration for them.

## What's here

- `researcher-agent.json` -- the agent definition (name, model, settings,
  system prompt, and the list of builtin tools it should have).
- `wf-research-tick.json` -- the worker's workflow graph, with literal (and
  deliberately stable) block UUIDs and three placeholders that `provision.sh`
  substitutes: `{{AGENT_ID}}`, `{{BASE}}`, `{{SERVICE_KEY}}`.
- `provision.sh` -- creates or updates both, then deploys the workflow.
  **Idempotent**: it looks up the agent and the workflow by `name` first, so
  re-running updates them instead of creating a second copy, it checks each
  tool's current attachment before attaching it again, and the stable block
  ids make a re-provision a diff rather than a rebuild.

## The worker

`wf-research-tick` runs once a minute and does one job per tick:

```
Tick (starter, schedule)
 |-> Sweep    requeue_stalled_research_jobs()  -- always, even on idle ticks
 \-> Claim    claim_research_jobs(1), then fetch the company, the brand's
     |        product_description + icp_notes, and the people to score, and
     |        build the agent prompt
     v
    HasJob (condition: <Claim.output.has_job> == True)
     | if
     v
    Research (agent block -> powacrm-researcher)
     v
    Record   extract the JSON object out of the agent's reply, then
             complete_research_job() -- or fail_research_job() with a
             readable reason
```

**One job per tick, not three.** An earlier draft fanned out three parallel
claim chains. Blocks in a Powabase workflow execute **sequentially** in
topological order -- measured on the live API, three sibling branches ran
strictly one after another, never overlapping -- so a fan-out would only make
each tick three times longer for the same throughput. Concurrency comes from
the 60-second schedule instead, and `claim_research_jobs` uses
`FOR UPDATE SKIP LOCKED` precisely so overlapping ticks cannot claim the same
row.

**Why `Record` parses instead of trusting the agent.** The researcher's system
prompt asks for a bare JSON object, and the model still narrates first --
Task 4's smoke test captured roughly a thousand characters of "I'll research
X..." plus a prose summary ahead of a ```json fence. Stripping fences is not
enough because the prose comes before the fence, so `Record` scans from the
first `{` that yields a parseable object through to the last `}`. If nothing
parses, it calls `fail_research_job` with the first 300 characters of the
reply rather than handing a prose blob to `complete_research_job`, whose
validation would reject it with a much less helpful message. This is not a
prompting bug to fix upstream -- a model narrating before it answers is normal
behaviour, and the extraction is the robust place to absorb it.

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
2. **Workflow block ids must be UUIDs.** A readable id like `"start"` makes
   `PUT /api/workflows/{id}/graph` return an opaque **HTTP 500** -- not a
   validation error naming the field. The ids in `wf-research-tick.json` are
   real UUIDs and are committed literally so they stay stable across
   re-provisions.
3. **A 200 from `PUT .../graph` does not mean your graph saved.** An empty
   `blocks` array is accepted and answers `200 {"blocks":0,"edges":0}`.
   `provision.sh` compares the returned counts against the definition and
   re-reads the workflow to confirm the starter block still carries its
   schedule keys.
4. **A `condition` block does not gate anything on its own.** It computes a
   route and reports it, but with only `sourceHandle` set on the outgoing
   edges, **both** branches still execute. What actually gates execution is
   the edge's own `condition` field (`"if"` / `"else"`, matching the route).
   Both are set in `wf-research-tick.json`; the `condition` field is the one
   that does the work. Getting this wrong would run the researcher agent --
   and spend credits -- on every idle tick.
5. **An `agent` block whose `agent_id` does not resolve does not fail.** It
   silently falls back to a default `gpt-4o-mini` with no system prompt and no
   tools, and cheerfully answers the research prompt with small talk. There is
   no error anywhere in the run. `provision.sh` therefore refuses to write the
   graph if the agent lookup by name came back empty.
6. **The `code` block sandbox is not ordinary Python.** `Exception` is not a
   defined name, so `except Exception as e:` raises `NameError` the moment
   anything actually goes wrong -- only a bare `except:` works, and the
   message comes from `sys.exc_info()[1]`. Dunder attribute access (including
   `type(e).__name__`) is rejected at compile time. The sandbox does have
   outbound network access and a pre-installed `requests`, which is what lets
   these blocks call the project's own RPCs directly.

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
