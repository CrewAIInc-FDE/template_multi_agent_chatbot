from crewai import LLM, Agent

from template_multi_agent_chatbot.types import ClassificationResult, Message


class MessageClassifierAgent:
    def __init__(self, messages: list[Message]):
        self._messages = messages

    def execute(self) -> str:
        messages = [
            {"role": m.role, "content": m.content} for m in self._messages[-10:]
        ]
        return (
            Agent(
                role="Message Classifier",
                goal="Classify the user's message intent into exactly one route.",
                backstory="""You are a triage agent that reads conversation history and picks the right route.
CRITICAL: When the user asks anything about CrewAI (the framework), always classify as CREWAI_DOCS.

ROUTING GUIDE:
- CREWAI_DOCS: Any question about CrewAI — agents, tasks, crews, flows, tools, deployment, or how to build with it.
- IMAGE_CREATION_UPDATE: Requests to generate or edit images.
- INTERNET_SEARCH: Questions needing up-to-date web information or current events.
- SIMPLE: Greetings, small talk, or anything answerable directly.""",
                llm=LLM(model="gemini/gemini-3-flash-preview"),
            )
            .kickoff(messages=messages, response_format=ClassificationResult)
            .pydantic.classification
        )
