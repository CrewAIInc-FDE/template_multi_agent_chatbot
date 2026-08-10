from typing import Any, Callable

from crewai.events.event_bus import crewai_event_bus

from template_multi_agent_chatbot.events.listeners import ConversationalEventListener
from template_multi_agent_chatbot.events.types import ImageGenerated


class ConversationalEventBus:
    """Flow-side helpers for anything a tool needs to push back to the UI.

    Built lazily by `ConversationalFlow.event_bus` — one per execution, which is
    one chat turn. The listener is only wired when this turn actually carries a
    webhook URL (local runs and `chat()` don't).
    """

    def __init__(self, flow: Any, webhook_url: str | None = None):
        self._flow = flow
        self._listener = (
            ConversationalEventListener.for_url(webhook_url) if webhook_url else None
        )

    def append_tool_message(self, content: str) -> None:
        """Record tool output in the transcript.

        The next turn's crew renders history into its task description, which is
        how the editing tool learns which image an edit refers to.
        """
        self._flow.append_message("tool", content)

    def store_image(self, image_base64: str) -> str:
        """Persist an image in flow state and return its `image#N` reference.

        Deliberately not a filesystem path: on AMP each turn is a separate
        execution and may run in a different container, so a `/tmp` file written
        this turn is unlikely to exist next turn. State survives; disk doesn't.
        """
        return self._flow.state.store_image(image_base64)

    def get_image(self, reference: str) -> str | None:
        return self._flow.state.get_image(reference)

    def emit_image_generated(self, source: Callable, image_base64: str) -> None:
        crewai_event_bus.emit(
            source=source,
            event=ImageGenerated(
                source_type=source.__name__,
                result={"image": image_base64},
            ),
        )
