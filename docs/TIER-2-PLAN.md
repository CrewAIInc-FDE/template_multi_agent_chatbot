# Tier 2 — Demo capability plan

How new capabilities get added to this chatbot, and the order worth building them in.

Companion docs: [CAPABILITIES.md](CAPABILITIES.md) (what exists today, business framing);
the demo-org PRDs live in the parent repo's `docs/`.

## The premise

This chatbot is the **front door to the demo org**. PRDs 1–3 are built in Studio;
PRDs 4 and 5 are specced as "OSS → deployed on AMP", which is exactly what this
repo is. So the OSS demos aren't separate projects — they're routes here.

That reframes the goal: don't build five apps, build one chat surface where a
demoer types a sentence and the right machinery runs.

## The recipe

Every capability is the same three edits. Nothing else changes, and existing
routes can't regress.

**1. Write the crew** — `src/template_multi_agent_chatbot/crews/your_crew.py`.
Follow `internet_search_crew.py`: one `_agent()`, one `_task(agent)`, one
`_crew()` that builds the agent **once** and shares it, `max_iter` and
`allow_delegation=False` on every agent.

**2. Add the handler** — `main.py`:

```python
@listen("YOUR_ROUTE")
def handle_your_route(self) -> str:
    """One line describing when to pick this route."""   # feeds the router catalog
    reply = YourCrew(messages=self.conversation_messages).execute()
    self.append_assistant_message(reply)
    return reply
```

The method name **must differ** from the route label or the handler re-triggers
itself into a loop.

**3. Register the route** — `routing/router_config.py`: add the label and
description to `ROUTE_DESCRIPTIONS`, its required credentials to
`ROUTE_REQUIREMENTS`, and the label to the `ConversationRoute` literal.

Then add a routing case to `tests/test_flow_bridge.py`.

> **Why `ROUTE_REQUIREMENTS` matters.** A route whose credentials are missing is
> dropped from the catalog, so it can never be selected. That's what lets one
> repo serve people who have Mongo, people who have Serper, and people who have
> neither — without any of them seeing a stack trace mid-demo.

## Build order

Sequenced by demo value per unit of work, not by PRD number.

### 1. Claims intake (PRD-5) — the flagship

The deepest technical demo and the one already fully specced. It's the only
candidate that exercises **async HITL**, which is the single most enterprise-relevant
CrewAI capability we don't currently show.

- **New CrewAI surface:** `@human_feedback(emit=[...])`, `Flow.from_pending()` /
  `resume()`, Pydantic + function guardrails, memory scopes
- **Shape:** extraction agent → guardrail-validated Pydantic claim → policy check
  → `@router`: auto-approve / human review / reject-with-letter
- **The moment:** the flow **pauses**, an approver responds, and the conversation
  resumes — potentially in a different process
- **Effort:** L — HITL inside a chat turn is genuinely new ground here
- **Risk:** the UI has no concept of a paused turn. Needs a "waiting for approval"
  state and a resume path. **Design this before writing the crew.**

### 2. Compliance reporter (PRD-4) — the engineering-credibility demo

- **New CrewAI surface:** chained guardrails incl. `HallucinationGuardrail`,
  unified `Memory` for "what changed since last run", knowledge sources
- **Shape:** research regulatory news → check against seeded policy docs →
  draft brief with a changes-since-last-run section → guardrail stack → structured JSON
- **Effort:** M — closest to the existing search crew, plus memory and guardrails
- **Dependency:** memory needs to persist across sessions, not just turns. Do the
  memory spike first (there's already a `spike-memory/` project in the parent repo)

### 3. Data analyst (NL2SQL) — best value for effort

Not in any PRD, and probably the highest-impact-per-hour item on this list.
"Ask a question about the database in English" lands with almost every audience
and needs one tool plus a seeded demo database.

- **New CrewAI surface:** `NL2SQLTool`
- **Effort:** S–M — mostly seeding a believable demo dataset
- **Risk:** guard the credentials hard. Read-only user, no write grants

### 4. Document Q&A

Upload a PDF, ask questions about it. Simple, tangible, and audience-supplied
documents make it feel real in a way canned demos don't.

- **New CrewAI surface:** `PDFSearchTool` or `knowledge_sources`
- **Effort:** M — needs an upload path in the UI, which no other route requires
- **Note:** this doubles as the "knowledge vs RAG tools" teaching moment

### 5. Hierarchical crew showcase

A manager agent delegating to specialists — visually the most "multi-agent"
thing CrewAI does, and currently invisible in this demo.

- **New CrewAI surface:** `Process.hierarchical`, `manager_agent`
- **Effort:** S — one crew, no new infrastructure
- **Caveat:** premature hierarchy is an official anti-pattern. This route should
  exist *because* it's a good demo, and the narration should say so honestly

### 6. MCP route

Connect a live external system with one line (`mcps=[...]`). Strongest when a
prospect's own stack is on the other end.

- **Effort:** S
- **Caveat:** MCP servers are untrusted input. Filter tools, don't expose whole
  servers

### Deliberately last: chat versions of PRDs 1–3

Lead qualifier, meeting prep and support triage are specced as **Studio** demos.
Chat versions would duplicate them rather than complement them. If built, frame
them as "same automation, two build paths" — proving the no-lock-in claim — not
as replacements. Lead qualifier is the natural one, since batch scoring via
`kickoff_for_each` is a genuinely different story from the Studio version.

## Cross-cutting work these will force

Worth doing once, deliberately, rather than three times badly:

| Concern | Trigger | Note |
|---|---|---|
| **Paused-turn UI** | Claims intake | No current concept of a turn awaiting a human |
| **Cross-session memory** | Compliance reporter | Distinct from the per-session transcript we persist today |
| **File upload** | Document Q&A | New surface in the Flask app |
| **Router accuracy at scale** | 6+ routes | Latency is handled; *accuracy* with a crowded catalog is not measured. Build a labelled routing eval before the catalog doubles |
| **Secret sprawl** | Every route | Each new route adds credentials. `ROUTE_REQUIREMENTS` keeps failures graceful, but the README needs a single table of what unlocks what |

## What I'd actually do first

**Claims intake, but design the paused-turn UX before writing any crew code.**
The HITL pause is the demo, and it's the one piece the current architecture has
no answer for — everything else on this list is a variation on things the repo
already does well.

If that feels too big for the next session, **NL2SQL is the best small win**: one
tool, one crew, one seeded database, and it demos beautifully.
