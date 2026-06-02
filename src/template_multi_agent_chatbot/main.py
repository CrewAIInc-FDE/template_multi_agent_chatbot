#!/usr/bin/env python

import os
from typing import Literal

from arize.otel import register
from crewai.flow import Flow, listen, or_, persist, router, start
from openinference.instrumentation.crewai import CrewAIInstrumentor

from template_multi_agent_chatbot.agents import (
    MessageClassifierAgent,
    SimpleResponseAgent,
)
from template_multi_agent_chatbot.crews import (
    CrewaiDocsCrew,
    ImageCreationCrew,
    InternetSearchCrew,
)
from template_multi_agent_chatbot.events import ConversationalEventBus
from template_multi_agent_chatbot.types import ConversationalState, Message

tracer_provider = register(
    api_key=os.getenv("ARIZE_API_KEY"),
    project_name=os.getenv("ARIZE_PROJECT_NAME"),
    space_id=os.getenv("ARIZE_SPACE_ID"),
)
CrewAIInstrumentor().instrument(tracer_provider=tracer_provider)


@persist()
class ConversationalFlow(Flow[ConversationalState]):
    @start()
    def load_initial_context(self):
        self.event_bus = ConversationalEventBus(self)
        self.state.messages.append(self.state.user_message)

    @router(load_initial_context)
    def classify_message(
        self,
    ) -> Literal["SIMPLE", "IMAGE_CREATION_UPDATE", "INTERNET_SEARCH", "CREWAI_DOCS"]:
        return MessageClassifierAgent(messages=self.state.messages).execute()

    @listen("SIMPLE")
    def handle_simple_message(self):
        message = Message(
            role="assistant",
            content=SimpleResponseAgent(messages=self.state.messages).execute(),
        )
        self.state.messages.append(message)
        return message

    @listen("IMAGE_CREATION_UPDATE")
    def handle_image_creation(self):
        message = Message(
            role="assistant",
            content=ImageCreationCrew(
                messages=self.state.messages,
                event_bus=self.event_bus,
                source=self.handle_image_creation,
            ).execute(),
        )
        self.state.messages.append(message)
        return message

    @listen("INTERNET_SEARCH")
    def handle_internet_search(self):
        message = Message(
            role="assistant",
            content=InternetSearchCrew(
                messages=self.state.messages,
            ).execute(),
        )
        self.state.messages.append(message)
        return message

    @listen("CREWAI_DOCS")
    def handle_crewai_docs(self):
        execution_result = CrewaiDocsCrew(
            user_message=self.state.user_message,
            messages=self.state.messages,
        ).execute()
        message = Message(role="assistant", content=execution_result.agent_response)
        self.state.messages.append(message)
        return execution_result

    @listen(
        or_(
            handle_simple_message,
            handle_image_creation,
            handle_internet_search,
            handle_crewai_docs,
        )
    )
    def finalize(self):
        return self.state.model_dump()


def kickoff():
    ConversationalFlow().kickoff(
        inputs={
            "user_message": {
                "role": "user",
                # "content": "Hello, how are you?",  # SIMPLE ROUTE
                # "content": "Generate an image of an otter playing with a ball",  # IMAGE ROUTE
                # "content": "Do a quick search about retro emulation",  # INTERNET SEARCH ROUTE
                "content": "How do I create a crew with custom tools in CrewAI?",  # CREWAI DOCS ROUTE
            },
        }
    )


def plot():
    ConversationalFlow().plot()


if __name__ == "__main__":
    kickoff()
