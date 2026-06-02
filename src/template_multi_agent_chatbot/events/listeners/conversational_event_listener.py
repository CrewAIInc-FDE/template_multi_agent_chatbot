from crewai.events import BaseEventListener
from crewai.events.types.llm_events import LLMThinkingChunkEvent

from template_multi_agent_chatbot.events.clients import Dispatcher
from template_multi_agent_chatbot.events.types import ImageGenerated


class ConversationalEventListener(BaseEventListener):
    def __init__(self, url: str):
        super().__init__()

        self._dispatcher = Dispatcher(url)

    def setup_listeners(self, crewai_event_bus):
        @crewai_event_bus.on(ImageGenerated)
        @crewai_event_bus.on(LLMThinkingChunkEvent)
        def on_event(source, event):
            self._dispatcher.dispatch(event)
