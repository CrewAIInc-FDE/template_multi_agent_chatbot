"""The AMP bridge: one kickoff == one conversational turn.

This is the highest-value test in the suite. AMP only exposes POST /kickoff, but
the conversational runtime hydrates a turn from a pending message that only
`handle_turn()` sets. Passing `user_message` in `inputs` alone is silently
dropped — no error, the handler just sees `current_user_message = None` and the
conversation is quietly broken. Nothing else catches that regression.
"""

import json
import uuid

import pytest


@pytest.fixture
def flow_module():
    import template_multi_agent_chatbot.main as main

    return main


@pytest.fixture
def stub_llm(monkeypatch):
    """Force a routing decision without calling a model."""
    from crewai.llms.providers.gemini.completion import GeminiCompletion

    route = {"value": "converse"}

    def fake_call(self, messages=None, **kwargs):
        if kwargs.get("response_format") or kwargs.get("response_model"):
            return json.dumps({"intent": route["value"]})
        return "conversational reply"

    monkeypatch.setattr(GeminiCompletion, "call", fake_call)
    return route


@pytest.fixture
def stub_crews(monkeypatch, flow_module):
    """Replace crews so no route makes a network call."""

    class StubCrew:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def execute(self):
            return "crew reply"

    for name in (
        "ImageCreationCrew",
        "InternetSearchCrew",
        "CrewaiDocsCrew",
        "SlackCrew",
    ):
        monkeypatch.setattr(flow_module, name, StubCrew, raising=False)
    return StubCrew


def _session_id() -> str:
    return f"test-{uuid.uuid4().hex[:12]}"


def test_kickoff_with_user_message_runs_a_turn(flow_module, stub_llm, stub_crews):
    stub_llm["value"] = "converse"
    flow = flow_module.ConversationalFlow()

    result = flow.kickoff(
        inputs={"id": _session_id(), "user_message": "hello there"}
    )

    assert flow.state.current_user_message == "hello there"
    assert result == "conversational reply"


def test_user_message_lands_in_the_transcript(flow_module, stub_llm, stub_crews):
    stub_llm["value"] = "converse"
    flow = flow_module.ConversationalFlow()

    flow.kickoff(inputs={"id": _session_id(), "user_message": "hello there"})

    roles = [message.role for message in flow.state.messages]
    assert roles == ["user", "assistant"]
    assert flow.state.messages[0].content == "hello there"


def test_router_dispatches_to_the_matching_handler(
    flow_module, stub_llm, stub_crews
):
    stub_llm["value"] = "INTERNET_SEARCH"
    flow = flow_module.ConversationalFlow()

    result = flow.kickoff(
        inputs={"id": _session_id(), "user_message": "what's in the news?"}
    )

    assert flow.state.last_intent == "INTERNET_SEARCH"
    assert result == "crew reply"


def test_history_survives_across_separate_flow_instances(
    flow_module, stub_llm, stub_crews
):
    """Each AMP turn is a fresh process. Class-level @persist plus a stable
    inputs["id"] is what restores the conversation; a terminal-step @persist
    would save but never restore, reducing every chat to a single turn."""
    stub_llm["value"] = "converse"
    session_id = _session_id()

    first = flow_module.ConversationalFlow()
    first.kickoff(inputs={"id": session_id, "user_message": "first message"})

    second = flow_module.ConversationalFlow()
    second.kickoff(inputs={"id": session_id, "user_message": "second message"})

    contents = [message.content for message in second.state.messages]
    assert "first message" in contents
    assert "second message" in contents


def test_separate_sessions_do_not_share_history(flow_module, stub_llm, stub_crews):
    stub_llm["value"] = "converse"

    first = flow_module.ConversationalFlow()
    first.kickoff(inputs={"id": _session_id(), "user_message": "session one"})

    second = flow_module.ConversationalFlow()
    second.kickoff(inputs={"id": _session_id(), "user_message": "session two"})

    assert "session one" not in [m.content for m in second.state.messages]


def test_kickoff_without_user_message_does_not_start_a_turn(flow_module, stub_llm):
    """handle_turn() re-enters kickoff() with only {"id": ...}. That call is the
    recursion base case: it must fall through and execute the graph — which is
    how the turn actually runs — without hydrating a *second* user turn.
    """
    stub_llm["value"] = "converse"
    flow = flow_module.ConversationalFlow()

    flow.kickoff(inputs={"id": _session_id()})

    assert flow.state.current_user_message is None
    assert [m for m in flow.state.messages if m.role == "user"] == []


def test_router_context_is_bounded(flow_module, stub_llm, stub_crews):
    """The default router context JSON-dumps the whole transcript plus every
    conversation event, so routing latency grows with conversation length."""
    stub_llm["value"] = "converse"
    session_id = _session_id()
    flow = flow_module.ConversationalFlow()

    for i in range(6):
        flow = flow_module.ConversationalFlow()
        flow.kickoff(inputs={"id": session_id, "user_message": f"message {i}"})

    context = flow.build_router_context()

    assert len(context["message_history"]) <= flow_module.ROUTER_HISTORY_WINDOW
    assert "events" not in context


def test_slack_route_dispatches_to_its_handler(flow_module, stub_llm, stub_crews):
    """A new route is three edits; this pins that the third one actually wired up."""
    stub_llm["value"] = "SLACK"
    flow = flow_module.ConversationalFlow()

    result = flow.kickoff(
        inputs={"id": _session_id(), "user_message": "what did the team say about pricing?"}
    )

    assert flow.state.last_intent == "SLACK"
    assert result == "crew reply"
