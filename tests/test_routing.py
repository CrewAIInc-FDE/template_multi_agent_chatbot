"""Router catalog behaviour.

The catalog is built at import time from environment credentials, so each test
reloads the module under a specific environment rather than mutating a global.
"""

import importlib
import typing

import pytest


def _reload_routing(monkeypatch, env: dict[str, str | None]):
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    import template_multi_agent_chatbot.routing.router_config as rc

    return importlib.reload(rc)


MONGO_ENV = {
    "MONGODB_CONNECTION_STRING": "mongodb://localhost",
    "MONGODB_DATABASE_NAME": "docs",
    "MONGODB_COLLECTION_NAME": "chunks",
}


def test_route_disabled_when_credentials_missing(monkeypatch):
    rc = _reload_routing(monkeypatch, dict.fromkeys(MONGO_ENV))
    assert "CREWAI_DOCS" not in rc.ROUTES
    assert "INTERNET_SEARCH" in rc.ROUTES


def test_route_enabled_when_credentials_present(monkeypatch):
    rc = _reload_routing(monkeypatch, MONGO_ENV)
    assert "CREWAI_DOCS" in rc.ROUTES


def test_partial_credentials_still_disable_the_route(monkeypatch):
    """Half-configured is not configured — the tool would fail at call time."""
    partial = dict(MONGO_ENV)
    partial["MONGODB_COLLECTION_NAME"] = None
    rc = _reload_routing(monkeypatch, partial)
    assert "CREWAI_DOCS" not in rc.ROUTES


def test_response_format_matches_enabled_routes(monkeypatch):
    """Constrained decoding must not offer a route the flow can't run."""
    rc = _reload_routing(monkeypatch, dict.fromkeys(MONGO_ENV))
    allowed = set(typing.get_args(rc.ConversationRoute.model_fields["intent"].annotation))
    assert allowed == {"converse", *rc.ROUTES}
    assert "CREWAI_DOCS" not in allowed


def test_router_config_never_passes_an_empty_route_list(monkeypatch):
    """With every credential absent no custom route survives. `routes` must still
    be non-empty: CrewAI treats an empty list as "infer the catalog from @listen
    labels", which would resurrect every route the credential check just removed.
    """
    import template_multi_agent_chatbot.routing.router_config as router_config

    # Derived, not hardcoded: a new route adding a new credential would otherwise
    # leave this test quietly passing against a stale list.
    every_credential = {
        variable
        for requirements in router_config.ROUTE_REQUIREMENTS.values()
        for variable in requirements
    }
    rc = _reload_routing(monkeypatch, dict.fromkeys(every_credential))

    assert rc.ROUTES == ()
    assert rc.ROUTER_CONFIG.routes == ("converse",)


def test_system_prompt_lists_only_enabled_capabilities(monkeypatch):
    """Guards the hallucinated-capability regression: the assistant described
    agents that did not exist because the prompt never stated what it could do."""
    rc = _reload_routing(monkeypatch, dict.fromkeys(MONGO_ENV))
    prompt = rc.CHAT_SYSTEM_PROMPT
    assert rc.ROUTE_DESCRIPTIONS["INTERNET_SEARCH"] in prompt
    assert rc.ROUTE_DESCRIPTIONS["CREWAI_DOCS"] not in prompt
    assert "never invent" in prompt.lower()


@pytest.mark.parametrize("field", ["default_intent", "fallback_intent"])
def test_unroutable_turns_fall_back_to_converse(monkeypatch, field):
    rc = _reload_routing(monkeypatch, dict.fromkeys(MONGO_ENV))
    assert getattr(rc.ROUTER_CONFIG, field) == "converse"


SLACK_ENV = {"CREWAI_PLATFORM_INTEGRATION_TOKEN": "platform-token"}


def test_slack_route_requires_the_platform_token(monkeypatch):
    rc = _reload_routing(monkeypatch, {**dict.fromkeys(MONGO_ENV), **dict.fromkeys(SLACK_ENV)})
    assert "SLACK" not in rc.ROUTES


def test_slack_route_enabled_with_the_platform_token(monkeypatch):
    rc = _reload_routing(monkeypatch, {**dict.fromkeys(MONGO_ENV), **SLACK_ENV})
    assert "SLACK" in rc.ROUTES
    allowed = set(typing.get_args(rc.ConversationRoute.model_fields["intent"].annotation))
    assert "SLACK" in allowed
