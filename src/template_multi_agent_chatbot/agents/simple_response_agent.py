from crewai import LLM, Agent

from template_multi_agent_chatbot.types import Message


class SimpleResponseAgent:
    def __init__(self, messages: list[Message]):
        self._messages = messages

    def execute(self) -> str:
        messages = [
            {"role": m.role, "content": m.content} for m in self._messages[-10:]
        ]
        return (
            Agent(
                role="Conversational Assistant",
                goal="Respond naturally and directly to greetings, small talk, and "
                "general questions.",
                backstory="""You are a friendly conversational assistant. Your text output is
streamed live to the user, so write as if speaking to them directly.
Read the history to avoid repeating greetings or phrases.
CRITICAL: respond solely in the same language the user is using.""",
                llm=LLM(model="gemini/gemini-3-flash-preview", stream=True),
            )
            .kickoff(messages=messages)
            .raw
        )
