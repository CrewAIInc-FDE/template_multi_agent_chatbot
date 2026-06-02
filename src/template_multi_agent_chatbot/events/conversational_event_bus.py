from typing import Callable

from crewai.events.event_bus import crewai_event_bus

from template_multi_agent_chatbot.events.listeners import ConversationalEventListener
from template_multi_agent_chatbot.events.types import ImageGenerated
from template_multi_agent_chatbot.types import Message


class ConversationalEventBus:
    def __init__(self, flow: object):
        self._flow = flow
        # TODO: Get rid of it after the AMP bug around webhook streaming is fixed
        if flow.state.webhook_url:
            self._listener = ConversationalEventListener(url=flow.state.webhook_url)

    def append_message(self, message: Message):
        self._flow.state.messages.append(message)

    def emit_image_generated(self, source: Callable, image_base64: str):
        crewai_event_bus.emit(
            source=source,
            event=ImageGenerated(
                source_type=source.__name__,
                result={"image": image_base64},
            ),
        )
