#!/usr/bin/env python
"""Does a user bearer token scope CrewAI Platform integrations to that person?

Accepting the token proves nothing on its own — the question is whether results
differ from the org token. This runs the same calls under both and compares.

    CREWAI_PLATFORM_INTEGRATION_TOKEN=<org token> \
    CREWAI_USER_BEARER_TOKEN=<user token> \
    python scripts/user_token_probe.py

Interpretation:
  identical results  -> the token is accepted but NOT scoped; per-user needs our
                        own OAuth app
  different results  -> scoping is real; wire it through per turn (see the note
                        on the shim at the bottom of the output)
"""

import json
import os
import sys

import requests


def base_url() -> str:
    root = os.getenv("CREWAI_PLUS_URL", "https://app.crewai.com")
    return f"{root}/crewai_plus/api/v1/integrations"


def call(token: str, path: str, **kwargs) -> tuple[int, object]:
    try:
        response = requests.request(
            kwargs.pop("method", "GET"),
            f"{base_url()}{path}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=45,
            **kwargs,
        )
    except Exception as exc:
        return 0, f"{type(exc).__name__}: {exc}"
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, response.text[:300]


def action_names(token: str, app: str) -> tuple[int, list[str]]:
    status, body = call(token, "/actions", params={"apps": app})
    if status != 200 or not isinstance(body, dict):
        return status, []
    return status, sorted(a.get("name", "") for a in body.get("actions", {}).get(app, []))


def identity(token: str) -> object:
    """Whatever the API will tell us about who this token represents."""
    for path in ("/me", "/users/me", "/whoami"):
        status, body = call(token, path)
        if status == 200:
            return {path: body}
    return "(no identity endpoint responded)"


def main() -> int:
    org = os.getenv("CREWAI_PLATFORM_INTEGRATION_TOKEN", "").strip()
    user = os.getenv("CREWAI_USER_BEARER_TOKEN", "").strip()
    app = os.getenv("PROBE_APP", "slack")

    if not org or not user:
        print("Set both CREWAI_PLATFORM_INTEGRATION_TOKEN and CREWAI_USER_BEARER_TOKEN.")
        return 2
    if org == user:
        print("Both tokens are identical — nothing to compare.")
        return 2

    print(f"Comparing org vs user token against '{app}'\n")

    org_status, org_actions = action_names(org, app)
    user_status, user_actions = action_names(user, app)
    print(f"  /actions  org  -> {org_status}, {len(org_actions)} actions")
    print(f"  /actions  user -> {user_status}, {len(user_actions)} actions")

    if user_status != 200:
        print("\n=> The user token is NOT accepted by the integrations API.")
        return 1

    only_org = set(org_actions) - set(user_actions)
    only_user = set(user_actions) - set(org_actions)
    if only_org or only_user:
        print(f"  catalogs differ: org-only={sorted(only_org)[:5]} user-only={sorted(only_user)[:5]}")
    else:
        print("  catalogs identical")

    # The real test: run a read and see whether the DATA differs.
    action = os.getenv("PROBE_ACTION", "SLACK_LIST_ALL_CHANNELS")
    print(f"\nExecuting {action} under each token...")
    payloads = {}
    for label, token in (("org", org), ("user", user)):
        status, body = call(
            token,
            f"/actions/{action}/execute",
            method="POST",
            json={"integration": {"_noop": True}},
        )
        text = json.dumps(body, sort_keys=True) if not isinstance(body, str) else body
        payloads[label] = text
        print(f"  {label:4} -> {status}, {len(text)} bytes")

    same = payloads["org"] == payloads["user"]
    print(f"\nRESULTS IDENTICAL: {same}")
    if same:
        print(
            "=> Accepted but not scoped: both tokens see the same data, so this\n"
            "   cannot power per-user Slack. Our own OAuth app is still required."
        )
    else:
        print(
            "=> Scoping is real. To use it per turn, crewai_tools must read the\n"
            "   token from context rather than os.getenv — patch\n"
            "   crewai_platform_tool_builder and crewai_platform_action_tool\n"
            "   (they bind the symbol at import, so patching misc alone is a no-op)."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
