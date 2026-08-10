# Demo Capabilities

Everything this chatbot can invoke, and the business case each piece demonstrates.
Written for anyone in the org running a demo — no code reading required.

Every user message goes through **one LLM router** that picks exactly one route.
The prospect sees a single chat box; underneath, four different execution patterns
are competing for the turn. That contrast *is* the demo: same interface, radically
different machinery, chosen live.

## At a glance

| Route | What it demonstrates | Cost per turn | Live? |
|---|---|---|---|
| `converse` | Instant grounded chat, no orchestration overhead | 1 small LLM call | ✅ |
| `IMAGE_CREATION_UPDATE` | Multimodal generation **and** stateful iterative editing | 1 agent + 1–2 image calls | ✅ |
| `INTERNET_SEARCH` | Real-time external data with mandatory citations | 1 agent + N search/scrape calls | ✅ |
| `CREWAI_DOCS` | Grounded RAG over a private corpus, with eval hooks | 1 agent + N vector searches | ⛔ needs MongoDB Atlas |

A route whose credentials are missing is **never offered to the router**, so the demo
degrades gracefully instead of erroring in front of a prospect.

---

## 1. `converse` — conversational baseline

**Business case:** the control case. Most "AI chatbot" products stop here. Showing it
side by side with the other three makes the orchestration argument without a slide.

- **Agent:** none — a direct LLM call (CrewAI's built-in `converse` handler)
- **Model:** `gemini-3.1-flash-lite`, streamed token by token
- **Tools:** none

**Demo moment:** ask *"what can you do?"*. The answer is generated from the routes
that are actually enabled in this environment, so it can never over-promise a
capability that isn't wired up. Worth calling out explicitly — self-describing
agents that hallucinate their own tooling is a common failure prospects have seen.

---

## 2. `IMAGE_CREATION_UPDATE` — multimodal creative work

**Business case:** marketing/creative asset generation with **iterative refinement**.
The differentiator isn't generating an image — everyone has that. It's that the
system remembers what it made and can modify *that specific asset* several turns
later.

- **Crew:** `ImageCreationCrew`
- **Agent:** *CrewAI Image Creation Assistant* — `gemini-3.1-pro-preview`, streamed, `max_iter=8`
- **Skill:** `image-generation` (prompt-crafting playbook + hard rules on never leaking internals)
- **Tools:**
  - `nano_banana_image_generation` — text → image (`gemini-3.1-flash-image`)
  - `nano_banana_image_editing` — image + instruction → revised image

**Demo script:** *"Generate an image of a cat on the beach."* → then *"now add
sunglasses."* The second turn is the point: it edits the existing cat rather than
generating a new one.

**Under the hood, worth knowing:** generated images live in conversation state, not
on disk, addressed by an internal handle (`image#1`, `image#2`). That's what makes
the edit survive when the next turn runs on a different machine. Images are capped
at the 4 most recent to bound state size.

---

## 3. `INTERNET_SEARCH` — real-time external research

**Business case:** answering questions whose truth changed after the model's training
cutoff — pricing, competitors, news, releases. Every answer carries source URLs,
which is the compliance-friendly framing procurement teams ask about.

- **Crew:** `InternetSearchCrew`
- **Agent:** *CrewAI Internet Research Assistant* — `gemini-3.1-pro-preview`, streamed, `max_iter=8`
- **Skill:** `internet-searching` (query formulation and source-evaluation techniques)
- **Tools:**
  - `search_the_internet_with_serper` — web search (Serper)
  - `read_website_content` — full-page scrape of promising results

**Demo moment:** the activity log shows the agent search → read → search again →
read. That visible multi-step loop is the "this is an agent, not a completion"
proof point. Typical turn: 2 searches + 2 scrapes in a few seconds.

The agent is instructed to **never fabricate a URL it didn't visit** and to append
sources to every answer.

---

## 4. `CREWAI_DOCS` — grounded RAG *(currently disabled)*

**Business case:** the highest-value enterprise pattern here — answering from a
*private* corpus rather than the open web. Swap CrewAI's docs for a customer's
policies, contracts, or knowledge base and this is the architecture most enterprise
deals are actually about.

- **Crew:** `CrewaiDocsCrew`
- **Agent:** *CrewAI Documentation Expert* — `gemini-3.1-pro-preview`, streamed, `max_iter=8`
- **Skill:** `crewai-docs` (query decomposition, reformulate-and-retry, honest "not in the docs")
- **Tool:** `TrackedMongoDBVectorSearchTool` — MongoDB Atlas vector search, 3072-dim embeddings, top 10 chunks

**Why it's worth re-enabling:** the tool records every **query → retrieved chunks**
pair. That's a ready-made RAG evaluation dataset — retrieval quality becomes
measurable rather than anecdotal, which is exactly the question a technical buyer
asks after the demo lands.

**To enable:** set `MONGODB_CONNECTION_STRING`, `MONGODB_DATABASE_NAME`,
`MONGODB_COLLECTION_NAME` against a cluster with the docs embedded. The route
self-enables on next start. Until then, CrewAI questions fall through to
`INTERNET_SEARCH`, which answers them acceptably by scraping the public docs.

---

## Shared infrastructure

Every route rides on these, and each is independently demoable.

**Routing.** One `gemini-3.1-flash-lite` call per turn, constrained to a fixed set of
labels. Route descriptions are generated from the handlers themselves, so the router
can't drift out of sync with what exists. The router sees only the last 4 messages —
deliberately, so routing latency stays flat instead of growing with conversation
length.

**Memory across turns.** Each turn is a separate execution; the transcript is restored
by session id. Nothing is held in memory between turns, which is what lets the same
conversation survive process restarts and horizontal scaling.

**Live event stream.** The UI reflects work as it happens: routing decision, tool
started/finished with durations, token-by-token streaming, model reasoning, and
delivered images. Prospects consistently react to the visible activity log more than
to the final answer — it makes the agent's process legible instead of a black box.

**Observability.** Every run is traced to Arize (OpenInference) plus CrewAI's own
tracing. The answer to "how would we debug this in production?" is a screen you can
show, not a promise.

---

## Adding a use case

The cost of a new vertical is the number worth quoting:

1. Write a crew (agent + task + tools) — the only real work
2. Add an `@listen("YOUR_ROUTE")` handler with a one-line docstring
3. Add the label and its description to `routing/router_config.py`

The docstring becomes how the router decides to invoke it. No classifier retraining,
no routing logic to edit, no regression risk to existing routes — a disabled or
misconfigured route simply doesn't get offered.

The five PRDs in the demo-org repo (lead qualifier, meeting-prep concierge, support
triage, compliance reporter, claims intake) each map onto exactly this shape: one
crew, one handler, one description.

---

## Known limits — say these before a prospect finds them

- **`CREWAI_DOCS` is off** unless MongoDB is configured.
- **No authentication** on the web UI. Anyone with the URL can spend your API budget.
- **Single web worker.** Streaming state is in-process, so the UI does not scale
  horizontally as-is.
- **SQLite on ephemeral disk.** Chat history resets on redeploy — fine for demos,
  not for a shared org-wide record.
- **Image state is heavy.** ~1MB per generated image, up to 4 retained per session.
- **Token use grows with conversation length.** Each crew renders recent history into
  its task prompt; a research-heavy 4-turn session ran ~57k input tokens.
