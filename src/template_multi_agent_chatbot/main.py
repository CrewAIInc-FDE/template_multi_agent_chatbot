#!/usr/bin/env python

import os
from typing import Any
from uuid import uuid4

from arize.otel import register
from crewai.experimental.conversational import ConversationConfig
from crewai.flow import Flow, listen, persist
from openinference.instrumentation.crewai import CrewAIInstrumentor

from template_multi_agent_chatbot.crews import (
    CrewaiDocsCrew,
    ImageCreationCrew,
    InternetSearchCrew,
)
from template_multi_agent_chatbot.events import ConversationalEventBus
from template_multi_agent_chatbot.routing import (
    CHAT_LLM,
    CHAT_SYSTEM_PROMPT,
    ROUTER_CONFIG,
    ROUTER_HISTORY_WINDOW,
)
from template_multi_agent_chatbot.state import ChatbotState

tracer_provider = register(
    api_key=os.getenv("ARIZE_API_KEY"),
    project_name=os.getenv("ARIZE_PROJECT_NAME"),
    space_id=os.getenv("ARIZE_SPACE_ID"),
)
CrewAIInstrumentor().instrument(tracer_provider=tracer_provider)


# `@persist()` MUST stay class-level. Restoring a session on a fresh AMP process
# only happens when persistence is configured on the class; `@persist` on a
# terminal step saves state but never restores it, which silently reduces every
# conversation to a single turn.
@persist()
@ConversationConfig(
    system_prompt=CHAT_SYSTEM_PROMPT,
    llm=CHAT_LLM,
    router=ROUTER_CONFIG,
    # MUST stay False on AMP. Deferral suppresses the per-turn `flow_finished`
    # the UI finalizes on, and a session-spanning trace batch can't work anyway
    # when every turn is a separate execution in a separate process.
    defer_trace_finalization=False,
)
class ConversationalFlow(Flow[ChatbotState]):
    conversational = True

    # ------------------------------------------------------------------
    # AMP bridge
    # ------------------------------------------------------------------

    def kickoff(self, inputs: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        """Turn an AMP kickoff into a conversational turn.

        AMP only exposes `POST /kickoff`, but the conversational runtime hydrates
        a turn from `_pending_user_message`, which only `handle_turn()` sets.
        Passing `user_message` in `inputs` alone does nothing — the message is
        dropped and the handler sees `current_user_message = None`.

        So: a kickoff carrying `user_message` becomes one turn. `handle_turn()`
        re-enters this method with only `{"id": ...}`, which falls through to the
        normal path — that's the recursion base case, not an accident.
        """
        if inputs and inputs.get("user_message") is not None:
            payload = dict(inputs)
            user_message = payload.pop("user_message")
            # Per-execution, deliberately not persisted: a webhook URL restored
            # from an earlier turn would point at a stale listener.
            object.__setattr__(self, "_webhook_url", payload.get("webhook_url"))
            # Register the webhook listener up front. It can't wait for a handler
            # to touch it: `conversation_route_selected` fires before any handler
            # runs, and a `converse` turn never touches the bus at all.
            _ = self.event_bus
            return self.handle_turn(
                user_message,
                session_id=payload.get("id"),
                **kwargs,
            )
        return super().kickoff(inputs=inputs, **kwargs)

    @property
    def event_bus(self) -> ConversationalEventBus:
        """One bus per flow instance.

        Cached deliberately: the listener registers itself on the global CrewAI
        event bus with no deregistration, so rebuilding it per turn would stack
        listeners and multiply every dispatch across a `chat()` session.
        """
        bus = getattr(self, "_event_bus", None)
        if bus is None:
            bus = ConversationalEventBus(
                self, webhook_url=getattr(self, "_webhook_url", None)
            )
            object.__setattr__(self, "_event_bus", bus)
        return bus

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def build_router_context(self) -> dict[str, Any]:
        """Trim the router's view of the conversation.

        The default implementation JSON-dumps the full transcript plus every
        `ConversationEvent` into the router prompt, so routing gets slower every
        turn. Routing needs the current message and a little context, nothing more.
        """
        context = super().build_router_context()
        context["message_history"] = context["message_history"][-ROUTER_HISTORY_WINDOW:]
        context.pop("events", None)
        return context

    # ------------------------------------------------------------------
    # Route handlers
    #
    # The @listen label is the route name; the method name must differ from it or
    # the handler re-triggers itself. The docstring's first line feeds the router
    # catalog whenever routing/router_config.py doesn't override it.
    # ------------------------------------------------------------------

    @listen("IMAGE_CREATION_UPDATE")
    def handle_image_creation(self) -> str:
        """Create a new image, or edit an image made earlier in this chat."""
        reply = ImageCreationCrew(
            messages=self.conversation_messages,
            event_bus=self.event_bus,
            source=self.handle_image_creation,
        ).execute()
        self.append_assistant_message(reply)
        return reply

    @listen("INTERNET_SEARCH")
    def handle_internet_search(self) -> str:
        """Research something on the live web and answer with sources."""
        reply = InternetSearchCrew(messages=self.conversation_messages).execute()
        self.append_assistant_message(reply)
        return reply

    @listen("CREWAI_DOCS")
    def handle_crewai_docs(self) -> str:
        """Answer a question about the CrewAI framework from the official docs."""
        result = CrewaiDocsCrew(messages=self.conversation_messages).execute()
        # Retrieved chunks stay private: they feed RAG evals via the trace, not
        # the chat transcript.
        self.append_agent_result(
            "crewai_docs",
            result.agent_response,
            metadata={"query_chunks": result.query_chunks},
        )
        self.append_assistant_message(result.agent_response)
        return result.agent_response


def kickoff():
    """Run one turn exactly the way AMP will, so the deployed path is testable locally."""
    ConversationalFlow().kickoff(
        inputs={
            "id": str(uuid4()),
            # "user_message": "Hello, how are you?",  # converse
            # "user_message": "Generate an image of an otter playing with a ball",  # image
            # "user_message": "Do a quick search about retro emulation",  # search
            "user_message": "How do I create a crew with custom tools in CrewAI?",  # docs
        }
    )


def chat():
    """Local multi-turn REPL. Finalizes session traces on exit."""
    ConversationalFlow().chat(session_id=str(uuid4()))


def plot():
    ConversationalFlow().plot()


if __name__ == "__main__":
    kickoff()
