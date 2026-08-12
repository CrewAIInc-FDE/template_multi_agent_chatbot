"""Gmail/Calendar tools must act as the signed-in user, not a shared account."""

import pytest


@pytest.fixture
def tools():
    from template_multi_agent_chatbot.tools import google_tools

    return google_tools


def _capture_auth(monkeypatch, tools, payload=None):
    """Record the Authorization header each Google call goes out with."""
    sent = []

    class Response:
        ok = True
        status_code = 200

        def json(self):
            return payload if payload is not None else {"messages": []}

    def fake_get(path, headers=None, params=None, timeout=None):
        sent.append(headers["Authorization"])
        return Response()

    monkeypatch.setattr(tools.requests, "get", fake_get)
    return sent


def test_calls_google_as_the_signed_in_user(monkeypatch, tools):
    from template_multi_agent_chatbot import user_context

    sent = _capture_auth(monkeypatch, tools)
    monkeypatch.setattr(
        user_context, "access_token_for", lambda provider: "ya29-alice"
    )

    tools.GmailSearchTool()._run(query="from:sarah")

    assert sent == ["Bearer ya29-alice"]


def test_two_users_hit_google_with_different_tokens(monkeypatch, tools):
    """The whole point: same question, different mailbox."""
    from template_multi_agent_chatbot import user_context

    sent = _capture_auth(monkeypatch, tools)
    for token in ("ya29-alice", "ya29-bob"):
        monkeypatch.setattr(user_context, "access_token_for", lambda p, t=token: t)
        tools.GmailSearchTool()._run(query="from:sarah")

    assert sent == ["Bearer ya29-alice", "Bearer ya29-bob"]


def test_unconnected_user_gets_an_instruction_not_an_answer(monkeypatch, tools):
    from template_multi_agent_chatbot import user_context

    sent = _capture_auth(monkeypatch, tools)
    monkeypatch.setattr(user_context, "access_token_for", lambda provider: None)

    result = tools.GmailSearchTool()._run(query="anything")

    assert sent == [], "must not call Google without a user token"
    assert "Connect Google" in result


def test_expired_access_is_reported_as_reconnect(monkeypatch, tools):
    from template_multi_agent_chatbot import user_context

    class Denied:
        ok = False
        status_code = 401
        text = "invalid"

        def json(self):
            return {}

    monkeypatch.setattr(user_context, "access_token_for", lambda provider: "stale")
    monkeypatch.setattr(
        tools.requests, "get", lambda *a, **k: Denied()
    )

    result = tools.GmailSearchTool()._run(query="anything")

    assert "reconnect" in result.lower()


def test_calendar_also_acts_as_the_user(monkeypatch, tools):
    from template_multi_agent_chatbot import user_context

    sent = _capture_auth(monkeypatch, tools, payload={"items": []})
    monkeypatch.setattr(user_context, "access_token_for", lambda provider: "ya29-bob")

    tools.CalendarEventsTool()._run(days_ahead=3)

    assert sent == ["Bearer ya29-bob"]
