"""Startup probe for CrewAI Platform integrations.

`apps=[...]` fails soft: the tool builder catches every exception while fetching
action schemas, logs, and returns — so a missing token, an unconnected app, or a
network problem yields an agent with **zero tools and no error**. The agent then
politely explains it can't do the thing, which looks like a model failure rather
than a configuration one.

This probe makes that state visible by asking the same endpoint directly.
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

_probed: dict[str, int] = {}


def _base_url() -> str:
    root = os.getenv("CREWAI_PLUS_URL", "https://app.crewai.com")
    return f"{root}/crewai_plus/api/v1/integrations"


def action_counts(apps: list[str], timeout: int = 10) -> dict[str, int]:
    """Return how many actions each app exposes. Empty dict if the call fails."""
    token = os.getenv("CREWAI_PLATFORM_INTEGRATION_TOKEN", "").strip()
    if not token:
        return {}

    # Only the app name is meaningful to the API; 'slack/send_message' style
    # references still resolve under their app.
    names = sorted({app.split("/")[0] for app in apps})
    try:
        response = requests.get(
            f"{_base_url()}/actions",
            headers={"Authorization": f"Bearer {token}"},
            params={"apps": ",".join(names)},
            timeout=timeout,
        )
        response.raise_for_status()
        actions = response.json().get("actions", {})
    except Exception as exc:
        logger.warning("Could not reach the CrewAI Platform actions API: %s", exc)
        return {}

    return {
        app: len(actions.get(app, []) or []) if isinstance(actions, dict) else 0
        for app in names
    }


def warn_if_unavailable(apps: list[str]) -> None:
    """Log once per process when a Platform app resolves to no actions."""
    names = sorted({app.split("/")[0] for app in apps})
    unchecked = [name for name in names if name not in _probed]
    if not unchecked:
        return

    counts = action_counts(unchecked)
    if not counts:
        # Mark as probed anyway: retrying on every turn would add latency to a
        # path that is already degraded.
        _probed.update(dict.fromkeys(unchecked, 0))
        logger.warning(
            "Platform apps %s could not be verified — the agent may have no "
            "tools. Check CREWAI_PLATFORM_INTEGRATION_TOKEN.",
            ", ".join(unchecked),
        )
        return

    _probed.update(counts)
    for app, count in counts.items():
        if count:
            logger.info("Platform app '%s' exposes %d actions.", app, count)
        else:
            logger.warning(
                "Platform app '%s' exposes NO actions — connect it in the AMP "
                "dashboard, or the agent will silently be unable to use it.",
                app,
            )
