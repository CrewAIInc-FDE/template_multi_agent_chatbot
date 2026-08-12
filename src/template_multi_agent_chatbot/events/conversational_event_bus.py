import base64
import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from crewai.events.event_bus import crewai_event_bus
from crewai.experimental.conversational import ConversationMessage

logger = logging.getLogger(__name__)

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

        Appends a `ConversationMessage` rather than calling `Flow.append_message`,
        which is the legacy ChatState path: it pushes a raw dict into
        `state.messages`, typed `list[ConversationMessage]`, so pydantic emits a
        PydanticSerializationUnexpectedValue warning every time state is
        serialized — which is on every persisted method and every trace event.
        """
        self._flow.state.messages.append(
            ConversationMessage(role="tool", content=content)
        )

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
        event = ImageGenerated(
            source_type=source.__name__,
            result={"image": image_base64},
        )
        crewai_event_bus.emit(source=source, event=event)

        if self._listener is None:
            self._write_local_copy(image_base64)
            return

        # Deliver directly as well as emitting. Emitting alone is enough locally,
        # but on AMP the listener's bus registration doesn't fire — the image
        # never reached the browser even though the tool succeeded and the agent
        # announced it. This is our own event, so we can hand it to the webhook
        # ourselves instead of depending on bus delivery. The UI dedupes on
        # event_id, so an extra copy is harmless if the bus does fire.
        try:
            self._listener.dispatch(event)
        except Exception as exc:
            logger.warning("Direct image dispatch failed: %s", exc)

    def _write_local_copy(self, image_base64: str) -> None:
        """Save the image where a human can open it.

        Only when no webhook is configured — i.e. `uv run chat`, the probe, or a
        bare kickoff. Those have no browser to deliver the image to, so without
        this the agent cheerfully announces an image that exists solely as base64
        inside flow state.
        """
        try:
            directory = Path(tempfile.gettempdir()) / "crewai-chatbot-images"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{datetime.now().strftime('%H%M%S')}.png"
            path.write_bytes(base64.b64decode(image_base64))
            logger.info("Image saved locally: %s", path)
            print(f"\n[image saved: {path}]\n")
        except Exception as exc:
            logger.warning("Could not save image locally: %s", exc)
