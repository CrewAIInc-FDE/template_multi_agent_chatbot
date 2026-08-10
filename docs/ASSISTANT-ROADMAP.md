# Assistant roadmap

Turning this into **one assistant with a roster of specialist agents** — a single
chat where you can reach every agent on the team.

Not a launcher for standalone workflows. The demo-org PRDs are separate
deployments; nothing here depends on them.

Companion doc: [CAPABILITIES.md](CAPABILITIES.md) — what exists today.

## The shape

One conversational flow. Each agent is a route. The router picks by default; the
user can override by naming an agent. Adding an agent is a handler plus a
description — no change to existing agents, and a misconfigured one disappears
from the roster instead of failing mid-conversation.

| Agent | Status | Backed by |
|---|---|---|
| Conversation | ✅ live | built-in `converse` |
| Web research | ✅ live | Serper + scrape |
| Image | ✅ live | Nano Banana tools |
| **Knowledge / docs** | ⛔ built, disabled | MongoDB Atlas vector search |
| **Slack** | ▢ to build | Platform app or MCP |
| **Comms** (Gmail, Calendar) | ▢ to build | Platform apps |
| **Workspace** (Notion, Drive) | ▢ to build | MCP or Platform apps |
| **Data** (SQL) | ▢ to build | `NL2SQLTool` |
| **Engineering** (GitHub, Linear, Sentry) | ▢ to build | MCP |

## How integrations actually work

Two mechanisms, both settable on the same agent. Verified against the installed
1.15.14 source and the v1.15.14 docs.

### `apps=` — CrewAI Platform integrations

```python
Agent(
    role="Slack Assistant",
    apps=["slack"],                 # whole app
    # apps=["slack/send_message"],  # or one action
)
```

At kickoff the agent calls `GET {CREWAI_PLUS_URL}/actions?apps=slack` with
`Authorization: Bearer $CREWAI_PLATFORM_INTEGRATION_TOKEN` (base URL defaults to
`https://app.crewai.com`), then builds **one tool per returned action**.

- Connect the integration once via OAuth in the AMP dashboard; the code only
  needs the token.
- `app/action` narrows to a single action. More than one `/` raises
  `ValueError: Invalid app format ... Apps can only have one '/'`.
- The catalog of available apps is **not in the public docs** — it's in the AMP
  dashboard. Check there before promising an agent.

> **Fails silently.** `_fetch_actions()` catches every exception, logs, and
> returns. A bad token or unconnected app therefore yields an agent with **zero
> tools and no error** — it will simply claim it can't do things. This is the
> same class of problem as the empty Mongo connection string, but quieter.
> Mitigation below.

### `mcps=` — Model Context Protocol servers

```python
Agent(
    mcps=[
        "https://mcp.example.com/mcp?api_key=...",   # external server
        "https://api.weather.com/mcp#get_forecast",  # one tool from a server
        "notion",                                    # connected MCP, bare slug
        "stripe#list_invoices",                      # one tool from a connected MCP
        MCPServerStdio(command="npx", args=[...],    # full control
                       tool_filter=create_static_tool_filter(
                           allowed_tool_names=["read_file"]),
                       cache_tools_list=True),
    ],
)
```

Transports: stdio, HTTP, SSE. Filter tools rather than exposing whole servers,
cache tool lists, and treat every server as untrusted input — CrewAI ships a
dedicated security page on this.

### Which to use

**Platform apps where AMP supports the integration; MCP for everything else.**
Apps are less code and the OAuth is handled for you; MCP covers anything not in
the catalog and works without AMP. Both can sit on one agent, so an agent can mix
them freely.

The cost is two credential models to explain. Keep it manageable by putting every
requirement in `ROUTE_REQUIREMENTS` so the roster reflects what's actually wired
up, whichever mechanism provided it.

## Per-user integrations

Goal: an agent acts as **the person chatting**, not as one shared service
account. What follows is what the platform actually supports, verified against
the 1.15.14 source and the platform docs — the docs alone are misleading here.

### What exists

`crewai.context` carries a `_platform_integration_token` ContextVar with
`set_platform_integration_token()` and a `platform_context()` manager.
`get_platform_integration_token()` reads the ContextVar **first**, then falls
back to `CREWAI_PLATFORM_INTEGRATION_TOKEN`, and the value is captured in
`ExecutionContext` so it survives the flow's thread hops. Set it per turn and
everything downstream uses that user's token.

| Path | Honours the ContextVar? |
|---|---|
| `mcps=` (connected slugs, via `PlusAPI`) | ✅ |
| Skills registry | ✅ |
| `apps=` (Platform actions) | ❌ — `crewai_tools` reads the env var only |

`crewai_tools` defines its *own* `get_platform_integration_token()` that reads
`os.getenv`, carrying a literal `# TODO: Use context manager to get token`.
Closing that gap needs a shim, and it must patch the two **consuming** modules
(`crewai_platform_tool_builder`, `crewai_platform_action_tool`) — they bind the
symbol from `misc` at import, so patching `misc` alone does nothing.

### What does not exist

**CrewAI exposes no OAuth authorization-code flow to third-party apps.** Access
tokens are `client_credentials` only and explicitly "not tying those credentials
to a human user"; service accounts are org-scoped with no impersonation or
delegation. The Slack integration page is explicit: one workspace connection,
one enterprise token, every agent action from the same account.

The `user_bearer_token` wording in the platform docs — *"scope authentication to
the requesting user"* — describes AMP's own surfaces, where the platform already
knows who is signed in. It is not reachable from an external app like this one.

### The model we use

1. **Google SSO** establishes per-user identity (`sub`, not email, so a rename
   doesn't orphan tokens).
2. **Google integrations** (Gmail, Calendar, Drive) come from *incremental
   scopes on the same OAuth client* — no second OAuth app.
3. **Slack** gets its own OAuth app, giving genuine per-user Slack tokens.
4. **Everything else** (HubSpot, Notion, Jira, Zendesk…) stays on CrewAI `apps=`
   at org level, so we don't own an OAuth app per vendor.

> **Never put user tokens in kickoff inputs.** `Flow` merges non-`id` inputs into
> state, `@persist` writes state to disk, and state is deep-copied into every
> trace event — tokens would land in SQLite *and* Arize. Pass a `user_id`; have
> the flow fetch that user's tokens from a UI endpoint at turn start
> (authenticated with `WEBHOOK_TOKEN`) and hold them in memory only.

## Auto-route with explicit override

Default is the LLM router. The user can override by naming an agent — useful on a
call when you need a known outcome, and it's **genuinely zero latency** because it
skips the routing model entirely.

**Backend.** `ConversationalFlow.route_turn()` is already the seam:

```python
def route_turn(self, context):
    forced = getattr(self, "_forced_route", None)
    if forced in ROUTES:
        return forced          # explicit: no router call at all
    return super().route_turn(context)   # otherwise the LLM router decides
```

`_forced_route` comes off the kickoff inputs in the existing `kickoff()` override,
alongside `user_message` — the same place `webhook_url` is handled today.

**Frontend.** Two entry points to the same mechanism: an agent picker beside the
composer, and `@agent` autocomplete in the message box. Both set `route` in the
kickoff body. The route badge already renders the outcome, so the user can see
when auto-routing disagreed with what they expected.

**Roster endpoint.** The UI needs to know which agents exist, and the backend
already computes exactly that (`ROUTE_CATALOG`, filtered by credentials). Expose
it rather than hardcoding a list in JavaScript — otherwise the picker will offer
agents that aren't configured.

## Build order

**1. Slack agent** — the one you named, and the best test of the whole pattern
because it forces the integration decision. Start with `apps=["slack"]`; fall back
to MCP if the AMP catalog doesn't cover what you need. *Effort: S.*

**2. Explicit override + roster UI** — do this early, while there are few agents.
It's the difference between a demo that works and a demo you can *steer*. *Effort: M.*

**3. Knowledge agent** — already built, needs Atlas with the docs embedded. Worth
generalising beyond CrewAI docs to "our docs" so it holds any corpus. *Effort: S once
Mongo exists.*

**4. Comms agent** (Gmail + Calendar) — one agent, two apps. "What's on my calendar
and who emailed me about it" is the assistant moment people recognise. *Effort: S.*

**5. Data agent** (`NL2SQLTool`) — ask the database a question in English. Highest
impact per hour of anything on this list. Use a read-only credential. *Effort: S–M,
mostly seeding a believable dataset.*

**6. Workspace / engineering agents** — Notion, Drive, GitHub, Linear, Sentry via
MCP, once the pattern is proven. *Effort: S each.*

## Two things that will bite

**Routing accuracy, not latency.** Latency is handled — small model, trimmed
context, constrained decoding. *Accuracy* with a crowded roster is not measured at
all. "What did we decide about pricing?" could plausibly be Slack, docs, or web.
Before the roster passes ~6 agents, build a labelled routing eval: 30–50 real
phrasings with expected routes, run on every change. The explicit override is the
safety valve, but it shouldn't be the reason the demo works.

**One agent per domain, not per app.** Resist a route per integration. A "Comms"
agent holding Gmail and Calendar routes better than separate Gmail and Calendar
agents, because the router never has to split hairs the user didn't intend. Group
by how someone would *ask*, not by vendor.

## Immediate follow-up

Add a startup credential check that actually calls the platform `/actions`
endpoint and logs which agents came back with zero tools. Given `apps=` fails
silently, without this the first sign of a broken Slack connection is the agent
politely explaining it has no idea how to read Slack — mid-demo.
