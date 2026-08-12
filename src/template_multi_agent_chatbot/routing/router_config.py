"""Routing configuration for the conversational flow.

Everything that decides *which* handler answers a turn lives here, so router
latency can be tuned in one place.

## Latency notes

Route selection is one LLM round-trip per turn — it cannot be zero. The levers,
in rough order of impact:

1. **Prompt size.** `Flow.build_router_context()` hands the router a JSON dump of
   the whole conversation *plus* every `ConversationEvent`. That grows without
   bound over a long demo, so `ConversationalFlow` overrides it to send only the
   last `ROUTER_HISTORY_WINDOW` messages and drops `events`. This is the single
   biggest lever.
2. **Constrained decoding.** Without an explicit `response_format` the framework
   builds `{"intent": str}` and lists the valid labels in a field *description*.
   `ConversationRoute` below pins it to a `Literal` instead, so the model picks
   from an enum rather than free-forming a string.
3. **Model choice.** `ROUTER_LLM` should stay the smallest model that routes
   accurately — it only ever emits one short label.
4. **Skipping the call entirely.** `ConversationalFlow.route_turn()` fast-paths
   unambiguous messages in Python and only falls through to this config when the
   intent is genuinely unclear. That path *is* zero latency.
"""

import logging
import os
from typing import Literal

from crewai import LLM
from crewai.experimental.conversational import RouterConfig
from pydantic import create_model

logger = logging.getLogger(__name__)

# Small + fast: the router only ever emits one label.
ROUTER_LLM = LLM(model="gemini/gemini-3.1-flash-lite")

# The conversational model for the built-in `converse` route (greetings, small
# talk, follow-ups). Absorbs what SimpleResponseAgent used to carry.
CHAT_LLM = LLM(model="gemini/gemini-3.1-flash-lite", stream=True)

_CHAT_SYSTEM_PROMPT_TEMPLATE = """You are a friendly conversational assistant for a CrewAI demo.
Your text output is streamed live to the user, so write as if speaking to them directly.
Read the history to avoid repeating greetings or phrases.
CRITICAL: respond solely in the same language the user is using.

WHAT YOU CAN ACTUALLY DO
Besides chatting, this demo can hand a request off to one of these specialists:
{capabilities}

CRITICAL: that list is exhaustive. When asked what you can do, describe those
capabilities and nothing else — never invent agents, crews, tools, or team members
that are not listed above. If asked for something outside the list, say plainly
that this demo doesn't cover it."""

# How many recent messages the router sees. Small on purpose — routing needs the
# current message and a little context, not the transcript.
ROUTER_HISTORY_WINDOW = 4

# Route label -> description shown to the router. Adding a use case means adding
# a handler with a docstring; an entry here only overrides that docstring.
ROUTE_DESCRIPTIONS = {
    "IMAGE_CREATION_UPDATE": (
        "Create a new image, or edit/adjust an image made earlier in this chat."
    ),
    "INTERNET_SEARCH": (
        "Look something up on the live web: current events, news, prices, "
        "recent releases, anything needing up-to-date information."
    ),
    "CREWAI_DOCS": (
        "Any question about the CrewAI framework itself — agents, tasks, crews, "
        "flows, tools, deployment, or how to build with it."
    ),
    "SLACK": (
        "Anything about the team's Slack workspace: what was said or decided in "
        "a channel, finding a conversation or thread, who is in a channel, or "
        "who to ask about a topic."
    ),
    "COMMS": (
        "The user's OWN email or calendar: messages they received or sent, who "
        "contacted them, what is on their schedule, when a meeting is, or who is "
        "attending it."
    ),
}

# Each route needs a backing service. Without one, the handler dies mid-turn on
# whatever cryptic error that client raises (an unset MONGODB_CONNECTION_STRING
# surfaces as pymongo's "Empty host (or extra comma in host list)"). So a route
# whose credentials are missing is never offered to the router at all: the demo
# runs on whatever is configured and falls back to `converse` for the rest.
ROUTE_REQUIREMENTS = {
    "IMAGE_CREATION_UPDATE": ("GEMINI_API_KEY",),
    "INTERNET_SEARCH": ("SERPER_API_KEY",),
    "CREWAI_DOCS": (
        "MONGODB_CONNECTION_STRING",
        "MONGODB_DATABASE_NAME",
        "MONGODB_COLLECTION_NAME",
    ),
    # Presence of the token only proves we can call the Platform API — whether
    # Slack is actually connected is checked at run time by platform_health.
    "SLACK": ("CREWAI_PLATFORM_INTEGRATION_TOKEN",),
    # Per-user: the deployment needs no Google credentials of its own. What it
    # needs is the ability to fetch a user's token, which rides on the shared
    # webhook secret.
    "COMMS": ("WEBHOOK_TOKEN",),
}


def enabled_routes() -> dict[str, str]:
    """Route catalog filtered down to what this environment can actually run."""
    enabled, disabled = {}, {}
    for route, description in ROUTE_DESCRIPTIONS.items():
        missing = [
            var for var in ROUTE_REQUIREMENTS.get(route, ()) if not os.getenv(var)
        ]
        if missing:
            disabled[route] = missing
        else:
            enabled[route] = description

    for route, missing in disabled.items():
        logger.warning(
            "Route %s disabled — set %s to enable it.", route, ", ".join(missing)
        )

    return enabled


ROUTE_CATALOG = enabled_routes()
ROUTES = tuple(ROUTE_CATALOG)

# Built from the same catalog the router sees, so "what can you do?" answers with
# what is actually wired up in this environment rather than a hardcoded list that
# drifts — or, worse, an invented one.
CHAT_SYSTEM_PROMPT = _CHAT_SYSTEM_PROMPT_TEMPLATE.format(
    capabilities="\n".join(f"- {description}" for description in ROUTE_CATALOG.values())
    or "- (nothing else is configured — you can only chat)"
)

# Constrained router output, built from the routes that are actually available.
# `intent` matches `RouterConfig.intent_field`.
ConversationRoute = create_model(
    "ConversationRoute",
    intent=(Literal[("converse", *ROUTES)], ...),
)


ROUTER_CONFIG = RouterConfig(
    llm=ROUTER_LLM,
    # Domain framing only — the route catalog is built automatically.
    prompt=(
        "You are triaging one message in a chat with a CrewAI demo assistant.\n"
        "Any question about CrewAI the framework always goes to CREWAI_DOCS.\n"
        "Prefer 'converse' for greetings, thanks, and follow-ups about something "
        "already answered."
    ),
    # `or ("converse",)` matters: an empty `routes` makes CrewAI infer the catalog
    # from @listen labels instead, which would resurrect every route we just
    # disabled. Naming converse explicitly keeps a bare-credentials clone working
    # as a plain chatbot.
    routes=ROUTES or ("converse",),
    route_descriptions=ROUTE_CATALOG,
    response_format=ConversationRoute,
    default_intent="converse",
    fallback_intent="converse",
)
