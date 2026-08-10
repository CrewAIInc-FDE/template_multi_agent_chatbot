"""Image storage in flow state.

Images live in state rather than on disk so an edit still works when the next
turn runs in a different AMP container.
"""

from template_multi_agent_chatbot.state import MAX_STORED_IMAGES, ChatbotState


def test_store_returns_a_usable_reference():
    state = ChatbotState()
    reference = state.store_image("aaa")

    assert reference == "image#1"
    assert state.get_image(reference) == "aaa"


def test_references_increment():
    state = ChatbotState()
    assert [state.store_image(str(i)) for i in range(3)] == [
        "image#1",
        "image#2",
        "image#3",
    ]


def test_oldest_images_are_evicted():
    state = ChatbotState()
    for i in range(MAX_STORED_IMAGES + 2):
        state.store_image(f"data-{i}")

    assert len(state.images) == MAX_STORED_IMAGES
    assert state.get_image("image#1") is None
    assert state.get_image(f"image#{MAX_STORED_IMAGES + 2}") == f"data-{MAX_STORED_IMAGES + 1}"


def test_references_are_never_recycled_after_eviction():
    """A recycled reference would silently point an edit at the wrong image."""
    state = ChatbotState()
    seen = {state.store_image(f"data-{i}") for i in range(MAX_STORED_IMAGES * 3)}

    assert len(seen) == MAX_STORED_IMAGES * 3


def test_unknown_reference_returns_none():
    assert ChatbotState().get_image("image#99") is None


def test_images_survive_a_serialization_round_trip():
    """@persist stores state as JSON, so anything that doesn't round-trip is lost
    between turns."""
    state = ChatbotState()
    reference = state.store_image("payload")

    restored = ChatbotState.model_validate(state.model_dump())

    assert restored.get_image(reference) == "payload"
    assert restored.image_counter == state.image_counter


# ---------------------------------------------------------------------------
# Slack app selection
# ---------------------------------------------------------------------------


def test_slack_actions_are_read_only():
    """The allowlist IS the safety boundary: apps=["slack"] would expose all 167
    actions, including archiving conversations, deleting messages and enterprise
    user management."""
    from template_multi_agent_chatbot.crews import slack_crew

    apps = slack_crew.slack_apps()
    forbidden = (
        "send", "post", "create", "update", "delete", "archive", "invite",
        "remove", "add", "join", "leave", "rename", "upload", "pin", "set_",
        "kick", "share", "schedule", "edit",
    )

    assert apps, "the agent must have at least one action"
    for action in apps:
        verb = action.split("/", 1)[1]
        assert not verb.startswith(forbidden), f"{action} is not read-only"


def test_slack_never_requests_a_whole_app():
    """A bare 'slack' reference would pull in every action, writes included."""
    from template_multi_agent_chatbot.crews import slack_crew

    assert all("/" in action for action in slack_crew.slack_apps())


def test_slack_allowlist_is_not_mutated_by_callers():
    from template_multi_agent_chatbot.crews import slack_crew

    slack_crew.slack_apps().append("slack/send_message")

    assert "slack/send_message" not in slack_crew.slack_apps()
