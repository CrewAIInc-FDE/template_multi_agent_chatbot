"""Per-turn identity and credentials for the signed-in user.

The chatting user's OAuth tokens are held in a ContextVar for the duration of a
turn, so a tool can act as *that person* rather than as one shared service
account.

Why a ContextVar and not flow state: state is persisted by `@persist` and
deep-copied into every trace event, so anything stored there ends up in SQLite
and in Arize. A ContextVar lives only in memory for the turn — and it genuinely
reaches tool execution, including across the agent executor's thread hop,
because CrewAI copies contextvars into the threads it spawns.

Why the flow fetches rather than receives: `Flow` merges non-`id` kickoff inputs
into state, so tokens passed as inputs would be persisted and traced. The UI
sends a `user_id`; this module exchanges it for credentials over the existing
`WEBHOOK_TOKEN` shared secret.
"""

import contextvars
import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

_user_credentials: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "user_credentials", default={}
)
_user_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "user_id", default=None
)


def load_for_turn(user_id: str | None, credentials_url: str | None) -> None:
    """Fetch this user's credentials and bind them to the current context.

    Failures are logged and treated as "no credentials": an agent that falls back
    to shared access is a better outcome than a turn that dies, and the tools
    report the absence to the user themselves.
    """
    _user_id.set(user_id)
    _user_credentials.set({})

    if not user_id or not credentials_url:
        return

    token = os.getenv("WEBHOOK_TOKEN", "").strip()
    if not token:
        logger.warning("WEBHOOK_TOKEN unset; cannot fetch user credentials.")
        return

    try:
        response = requests.get(
            f"{credentials_url.rstrip('/')}/{user_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        response.raise_for_status()
        providers = response.json().get("providers", {})
    except Exception as exc:
        logger.warning("Could not fetch credentials for %s: %s", user_id, exc)
        return

    _user_credentials.set(providers)
    if providers:
        logger.info(
            "Loaded credentials for %s: %s", user_id, ", ".join(sorted(providers))
        )


def current_user_id() -> str | None:
    return _user_id.get()


def credentials_for(provider: str) -> dict[str, Any] | None:
    """The signed-in user's grant for a provider, or None if they haven't connected it."""
    return _user_credentials.get().get(provider)


def access_token_for(provider: str) -> str | None:
    grant = credentials_for(provider)
    return grant.get("access_token") if grant else None


def connected_providers() -> list[str]:
    return sorted(_user_credentials.get())
