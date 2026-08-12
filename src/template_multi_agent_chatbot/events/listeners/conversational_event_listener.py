from crewai.events import BaseEventListener
from crewai.events.types.flow_events import (
    ConversationRouteSelectedEvent,
    ConversationTurnCompletedEvent,
)
from crewai.events.types.llm_events import LLMThinkingChunkEvent

from template_multi_agent_chatbot.events.clients import Dispatcher


class ConversationalEventListener(BaseEventListener):
    """Pushes the events AMP won't relay straight to the UI's channel webhook.

    AMP relays the standard flow/LLM/tool events it knows about. These four it
    either doesn't relay (`llm_thinking_chunk`), delivers inconsistently (custom
    events like `ImageGenerated`), or has no support for at all — the two
    conversation events are part of CrewAI's experimental surface, so we don't
    assume AMP's relay list carries them.

    Use `for_url()` rather than constructing directly: registration on the global
    CrewAI event bus is permanent, so one instance per process is the only way to
    avoid stacking listeners.
    """

    _instance: "ConversationalEventListener | None" = None

    def __init__(self, url: str):
        super().__init__()

        self._dispatcher = Dispatcher(url)

    @classmethod
    def for_url(cls, url: str) -> "ConversationalEventListener":
        """Return the process-wide listener, retargeted at `url`.

        AMP keeps worker processes warm across kickoffs, so a listener built per
        turn would survive into the next one and every event would be dispatched
        once per turn the process had ever handled.
        """
        if cls._instance is None:
            cls._instance = cls(url)
        else:
            cls._instance._dispatcher = Dispatcher(url)
        return cls._instance

    def dispatch(self, event) -> None:
        """Send one event to the UI without going through the event bus.

        Bus delivery is not reliable inside the deployed runtime, so callers that
        own their event can deliver it directly.
        """
        self._dispatcher.dispatch(event)

    def setup_listeners(self, crewai_event_bus):
        # ImageGenerated is deliberately absent: the event bus delivers it
        # locally but not inside the deployed runtime, so ConversationalEventBus
        # hands it straight to `dispatch()`. Listening here as well would send it
        # twice locally, and these events carry no id the UI can dedupe on — the
        # user would simply see the image rendered twice.
        @crewai_event_bus.on(LLMThinkingChunkEvent)
        @crewai_event_bus.on(ConversationRouteSelectedEvent)
        @crewai_event_bus.on(ConversationTurnCompletedEvent)
        def on_event(source, event):
            self._dispatcher.dispatch(event)
