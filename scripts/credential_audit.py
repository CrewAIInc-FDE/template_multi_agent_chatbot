#!/usr/bin/env python
"""Report which agents act as the signed-in user and which use shared credentials.

The point of per-user OAuth is that Slack and email answers reflect *that
person's* access. That claim is easy to believe and hard to verify, so this
inspects each agent's actual tools and says plainly which identity they run as.

    python scripts/credential_audit.py
"""

import inspect
import io
import contextlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Tools that resolve credentials per user read this module.
PER_USER_MARKER = "user_context"


def _platform_identity() -> tuple[str, str]:
    """Platform tools follow the token shim: per-user when the shim is applied
    and the user has connected their own CrewAI account, org-wide otherwise."""
    from template_multi_agent_chatbot import platform_token_shim

    if platform_token_shim._applied:
        return (
            "per-user*",
            "user's CrewAI integration token when connected, else org token",
        )
    return ("shared", "CrewAI Platform app — org integration token")


def classify(tool) -> tuple[str, str]:
    """Return (identity, why) for one tool.

    Inspects the defining *module*, not just the class body: these tools resolve
    the token through a shared module-level helper, so looking only at the class
    reported them as identity-free — which was exactly backwards.
    """
    module = sys.modules.get(type(tool).__module__)
    try:
        source = inspect.getsource(module) if module else ""
    except (OSError, TypeError):
        source = ""

    if PER_USER_MARKER in source:
        return ("per-user", "reads the signed-in user's token at call time")
    if type(tool).__module__.startswith("crewai_tools.tools.crewai_platform_tools"):
        return _platform_identity()
    return ("n/a", "no user identity involved")


def main() -> int:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        from template_multi_agent_chatbot.crews import (
            CommsCrew,
            ImageCreationCrew,
            InternetSearchCrew,
            SlackCrew,
        )
        from template_multi_agent_chatbot.routing.router_config import ROUTES

        agents = {}
        for label, build in (
            ("COMMS", lambda: CommsCrew(messages=[])._agent()),
            ("SLACK", lambda: SlackCrew(messages=[])._agent()),
            ("INTERNET_SEARCH", lambda: InternetSearchCrew(messages=[])._agent()),
        ):
            try:
                agents[label] = build()
            except Exception as exc:
                agents[label] = exc

    print(f"{'route':18} {'identity':10} tool / reason")
    print("-" * 78)
    summary = {}
    for label, agent in agents.items():
        if isinstance(agent, Exception):
            print(f"{label:18} {'ERROR':10} {type(agent).__name__}: {agent}")
            continue

        tools = list(getattr(agent, "tools", []) or [])
        apps = list(getattr(agent, "apps", []) or [])
        rows = [(t.name, *classify(t)) for t in tools]
        for app in apps:
            rows.append((app, *_platform_identity()))

        if not rows:
            print(f"{label:18} {'-':10} (no tools)")
        for name, identity, why in rows:
            summary[identity] = summary.get(identity, 0) + 1
            print(f"{label:18} {identity:10} {name} — {why}")
        print()

    print("totals:", ", ".join(f"{k}={v}" for k, v in sorted(summary.items())))
    if summary.get("shared"):
        print(
            "\nNOTE: 'shared' tools act as one org-wide account — every user sees "
            "the same data."
        )
    if summary.get("per-user*"):
        print(
            "\nNOTE: 'per-user*' is conditional. These use the signed-in user's own "
            "CrewAI\nintegration token when they have connected one, and fall back to "
            "the shared org\ntoken otherwise. Store a token under the 'crewai_platform' "
            "provider to activate."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
