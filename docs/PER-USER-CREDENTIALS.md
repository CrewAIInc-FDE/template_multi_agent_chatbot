# Per-user credentials

Making an agent act as **the person chatting** rather than as one shared service
account, so Slack and email answers reflect that user's own access.

## Why this has to be ours

CrewAI cannot carry the identity for us. Verified against 1.15.14 source and the
platform API:

- **No OAuth authorization-code flow for third-party apps.** Access tokens are
  `client_credentials` only and explicitly "not tying those credentials to a
  human user". `security_config.py` lists impersonation/delegation as *TODO*.
- **`user_bearer_token` is not reachable from outside.** It appears in the
  platform docs but has zero hits in the OSS source, isn't in the kickoff API,
  and `/chat/start` **silently ignores** it (probed live: accepted, 200, no effect).
  It describes AMP's own Studio surfaces, where the platform already knows who is
  signed in.
- **`apps=` and MCP slug resolution both read the env var.** `crewai_tools`'
  `get_platform_integration_token()` is `os.getenv` only, carrying a literal
  `# TODO: Use context manager to get token`, and `crewai/mcp/tool_resolver.py`
  imports *that* function rather than the context-aware one in `crewai.context`.

So: identity is ours to carry, and only tools we write can consume it.

## The one thing that already works

A ContextVar set at the top of a turn **does** reach tool execution — proven, not
assumed:

```
set at flow-method depth : USER-TOKEN-abc123
seen inside tool _run    : USER-TOKEN-abc123
PROPAGATES TO TOOLS      : True
```

It survives the crew kickoff and the agent executor's thread hop, because CrewAI
copies contextvars across `asyncio.to_thread`. That is the load-bearing fact this
design rests on.

## Architecture

```
Google SSO ──> user_id (Google `sub`, stable across renames)
                 │
   UI stores per-provider tokens keyed by user_id
                 │
   kickoff inputs carry user_id ONLY  ──────────────> flow stashes it on the
   (never tokens — see below)                          instance, not in state
                                                             │
                            flow fetches that user's tokens from the UI
                            over WEBHOOK_TOKEN, holds them in a ContextVar
                                                             │
                                    our tools read the ContextVar at call time
```

> **Tokens must never travel in `inputs`.** `Flow` merges non-`id` inputs into
> state, `@persist` writes state to disk, and state is deep-copied into every
> trace event — tokens would land in SQLite *and* Arize. `user_id` is an opaque
> identifier and is stashed on the flow instance (like `_webhook_url`), so it
> never reaches state either.

## Phases

### A — Framework (unblocked, build first)

1. **Credential store** — new table in `frontend/.../db/__init__.py`, keyed by
   `(user_id, provider)`, holding access/refresh tokens, expiry and scopes.
   Follows the existing `_SCHEMA` + `init_db()` additive-migration pattern.
2. **Internal endpoint** — `GET /api/internal/credentials/<user_id>` on the Flask
   app, authenticated with the existing `WEBHOOK_TOKEN` shared secret (the same
   one AMP already holds), refreshing expired Google tokens before returning.
   Exempt from the session gate exactly like `/api/webhook`.
3. **Transport** — `send_message` adds `user_id` to kickoff inputs;
   `ConversationalFlow.kickoff()` stashes it as `_user_id` alongside `_webhook_url`.
4. **Flow-side context** — new `user_context.py`: fetches the user's credentials
   once per turn and exposes them through a ContextVar, with a
   `credentials_for(provider)` accessor for tools.
5. **Tests** — endpoint auth, store round-trip, transport carries `user_id`,
   tokens never appear in flow state.

### B — Google provider (unblocked)

6. **Incremental scopes** on the existing OAuth client — Gmail and Calendar
   requested on top of the sign-in consent, no second OAuth app. `access_type=offline`
   and `include_granted_scopes=true` are already set for exactly this.
7. **A "connect" affordance** in the UI so a user grants mail/calendar access
   separately from signing in.
8. **Custom Gmail/Calendar tools** that read `credentials_for("google")` at call
   time and act as that user. New route + agent, following the Slack crew shape.

### C — Slack per-user (blocked on you)

Requires a **Slack OAuth app** we own, issuing `xoxp-` user tokens — CrewAI's
integration cannot delegate. Once that exists, the same store/endpoint/context
machinery applies; only new tools are needed, replacing `apps=["slack/..."]`.

Until then the Slack agent stays org-level, which should be narrated honestly as
a shared workspace connection.

## Verification

- Unit: store round-trip, endpoint rejects a bad `WEBHOOK_TOKEN`, expired Google
  tokens refresh, `user_id` reaches the flow, **no token appears in `flow.state`
  or any trace payload**.
- Live: two people sign in, each connects their own Google, and the same question
  returns different results per user.

## Security posture, stated plainly

Tokens live in the UI's database and cross the network once per turn over TLS,
authenticated with `WEBHOOK_TOKEN`. Good enough for an internal demo; short of
what a production system wants, which would be a real secrets manager, encryption
at rest, and per-token revocation. Worth saying out loud when demoing to security-
minded audiences rather than implying otherwise.
