"""Conversation history rendering for crew task descriptions."""

from template_multi_agent_chatbot.crews.history import HISTORY_WINDOW, format_history


def test_roles_are_labelled():
    rendered = format_history(
        [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    )

    assert rendered == "[USER] hi\n[ASSISTANT] hello"


def test_only_the_most_recent_messages_are_kept():
    messages = [{"role": "user", "content": str(i)} for i in range(HISTORY_WINDOW + 5)]

    rendered = format_history(messages)

    assert len(rendered.splitlines()) == HISTORY_WINDOW
    assert "[USER] 4" not in rendered
    assert f"[USER] {HISTORY_WINDOW + 4}" in rendered


def test_tool_messages_are_included():
    """Image references reach the next turn's agent through history — dropping
    tool messages would break the generate-then-edit flow."""
    rendered = format_history([{"role": "tool", "content": "Generated image#1"}])

    assert "[TOOL] Generated image#1" in rendered


def test_missing_fields_do_not_raise():
    assert format_history([{}]) == "[USER] "


def test_empty_history():
    assert format_history([]) == ""
