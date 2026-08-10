# Multi-Agent Chatbot

![CrewAI Multi-Agent Chatbot Screenshot](README_screenshot.png)

A conversational chatbot built with [CrewAI Flows](https://docs.crewai.com), deployed to [CrewAI AMP](https://docs.crewai.com/en/enterprise), with a Discord-style Flask web UI.

## Architecture

![CrewAI Multi-Agent Chatbot Architecture](README_architecture.png)

Two independent apps that talk over HTTP:

- **Backend** (`src/template_multi_agent_chatbot/`) — a `ConversationalFlow` built on CrewAI's [conversational flows](https://docs.crewai.com/v1.15.14/en/guides/flows/conversational-flows) (`conversational = True` + `@ConversationConfig`), deployed to AMP. The LLM router in `routing/router_config.py` sends each turn to a route (`converse`, `IMAGE_CREATION_UPDATE`, `INTERNET_SEARCH`, `CREWAI_DOCS`); `converse` is the framework's built-in chat handler, the rest are `@listen` handlers backed by crews. Class-level `@persist()` restores the transcript per session id. Events AMP doesn't relay go out via `ConversationalEventBus` → `ConversationalEventListener` → `Dispatcher`.
- **Frontend** (`frontend/ui_template_multi_agent_chatbot/`) — a Flask app. It stores messages in SQLite, fires `POST /kickoff` to AMP, receives events back on `/api/webhook/<channel_id>`, and streams them to the browser over SSE.

**Request flow:** browser → Flask (`202`, saves to SQLite) → `POST /kickoff` to AMP → router picks a route → handler runs → events → Flask `/api/webhook/<channel_id>` → SSE → browser.

### One kickoff, one turn

AMP only exposes `POST /kickoff`, but the conversational runtime hydrates a turn from `handle_turn()` — passing `user_message` in `inputs` alone is silently dropped. `ConversationalFlow.kickoff()` bridges the two: a kickoff carrying `user_message` becomes one `handle_turn()`. Session continuity rides on a stable `inputs["id"]` (the channel's `conversation_id`) plus class-level `@persist()`, so any fresh AMP process restores the conversation.

Two settings that look optional but aren't, both documented at their definition in `main.py`: `@persist()` must stay **class-level** (a terminal-step `@persist` saves but never restores, silently reducing every chat to one turn), and `defer_trace_finalization` must stay **`False`** (deferral suppresses the per-turn `flow_finished` the UI finalizes on).

### Adding a use case

Add an `@listen("YOUR_ROUTE")` handler whose method name differs from the label, give it a one-line docstring, and add the label to `routing/router_config.py`. The docstring feeds the router's route catalog.

## Setup

**Requirements:** Python `>=3.11,<3.14` · [uv](https://docs.astral.sh/uv/) · [ngrok](https://ngrok.com/)

Backend and frontend have **separate** env files:

```bash
cp .env.example .env                   # backend (crew/flow, also set in AMP)
cp frontend/.env.example frontend/.env # frontend (Flask UI)
```

- **Backend `.env`:** `GEMINI_API_KEY`, `OPENAI_API_KEY` (docs embeddings), `SERPER_API_KEY`, `MONGODB_CONNECTION_STRING` / `MONGODB_DATABASE_NAME` / `MONGODB_COLLECTION_NAME`, `ARIZE_API_KEY` / `ARIZE_PROJECT_NAME` / `ARIZE_SPACE_ID`, `CREWAI_TRACING_ENABLED`.
- **Frontend `frontend/.env`:** `DEPLOYMENT_URL`, `DEPLOYMENT_KEY`, `PUBLIC_BASE_URL` (URL AMP calls back; needed in dev and prod). Dev-only: `PORT` and `NGROK_DOMAIN` (in prod Heroku injects `PORT` and `NGROK_DOMAIN` is unused).
- **`WEBHOOK_TOKEN`** lives in **both** files and must be identical (backend signs events, UI validates them).

Then:

```bash
crewai deploy   # deploy the flow to AMP
bin/start       # installs FE deps, starts Flask + ngrok on $PORT (default 5005)
```

Open the local URL, create a channel, and chat. AMP posts events straight to `<PUBLIC_BASE_URL>/api/webhook/<channel_id>`, authenticated with `WEBHOOK_TOKEN` — no third-party relay involved.

To exercise the flow without the UI:

```bash
uv run chat      # local multi-turn REPL against the flow
uv run kickoff   # one turn, using the exact inputs AMP sends
uv run plot      # render the flow graph
```

## Deploying the UI to Heroku

The UI is self-contained in `frontend/` (its own `pyproject.toml` / `uv.lock` — no CrewAI deps). The repo root stays reserved for AMP, so only `frontend/` is deployed, via the official `heroku/python` buildpack + `git subtree` (no third-party buildpacks).

```bash
# One-time
heroku create <your-ui-app>            # or: heroku git:remote -a <your-ui-app>
heroku buildpacks:set heroku/python -a <your-ui-app>
heroku config:set -a <your-ui-app> \
  DEPLOYMENT_URL=... DEPLOYMENT_KEY=... WEBHOOK_TOKEN=... \
  PUBLIC_BASE_URL=https://<your-ui-app>.herokuapp.com

# Deploy the frontend/ subdirectory (commit first)
git subtree push --prefix frontend heroku main
# If rejected (non-fast-forward):
git push heroku "$(git subtree split --prefix frontend main)":refs/heads/main --force
```

- `PORT` is injected by Heroku — don't set it.
- Run a **single web dyno** (`--workers 1`): SSE/response state is in-process.
- SQLite at `frontend/ui_template_multi_agent_chatbot/db/chatbot.db` is on ephemeral disk and resets on each deploy; use a managed DB for persistence.
