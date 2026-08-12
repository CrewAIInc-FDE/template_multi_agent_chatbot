"""Platform tools must be able to act as the signed-in user.

crewai_tools resolves the platform token from os.getenv only, carrying a literal
`# TODO: Use context manager to get token`, so apps=[...] is org-wide by default.
This bridges it to the ContextVar core already ships.
"""

import pytest


@pytest.fixture
def shim(monkeypatch):
    monkeypatch.setenv("CREWAI_PLATFORM_INTEGRATION_TOKEN", "ORG-TOKEN")
    from template_multi_agent_chatbot import platform_token_shim, user_context

    assert platform_token_shim.apply()
    user_context.load_for_turn(None, None)
    return user_context


def _resolvers():
    """The two modules that bind the getter at import and so must each be patched."""
    from crewai_tools.tools.crewai_platform_tools import (
        crewai_platform_action_tool,
        crewai_platform_tool_builder,
    )

    return (
        crewai_platform_action_tool.get_platform_integration_token,
        crewai_platform_tool_builder.get_platform_integration_token,
    )


def _set_user_token(user_context, token):
    user_context._user_credentials.set({"crewai_platform": {"access_token": token}})


def test_falls_back_to_the_org_token_without_a_user(shim):
    assert [resolve() for resolve in _resolvers()] == ["ORG-TOKEN", "ORG-TOKEN"]


def test_uses_the_signed_in_users_token_when_present(shim):
    _set_user_token(shim, "USER-TOKEN-alice")

    assert [resolve() for resolve in _resolvers()] == [
        "USER-TOKEN-alice",
        "USER-TOKEN-alice",
    ]


def test_each_user_resolves_to_their_own_token(shim):
    resolve = _resolvers()[0]

    _set_user_token(shim, "USER-TOKEN-alice")
    alice = resolve()
    _set_user_token(shim, "USER-TOKEN-bob")

    assert (alice, resolve()) == ("USER-TOKEN-alice", "USER-TOKEN-bob")


def test_both_consuming_modules_are_patched():
    """Patching `misc` alone is a no-op: both consumers imported the symbol."""
    from template_multi_agent_chatbot import platform_token_shim

    assert len(platform_token_shim._TARGETS) == 2
    assert all(resolve.__name__ == "resolve" for resolve in _resolvers())


def test_missing_configuration_still_raises(shim, monkeypatch):
    """The original contract raises when nothing is set, and callers rely on it
    to report a setup problem rather than failing later with an empty token."""
    monkeypatch.delenv("CREWAI_PLATFORM_INTEGRATION_TOKEN", raising=False)
    resolve = _resolvers()[0]

    with pytest.raises(ValueError, match="No platform integration token"):
        resolve()
