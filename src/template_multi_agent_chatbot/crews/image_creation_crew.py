from pathlib import Path
from typing import Any

from crewai import LLM, Agent, Crew, Process, Task
from crewai.utilities.types import LLMMessage

from template_multi_agent_chatbot.crews.history import format_history, utc_now
from template_multi_agent_chatbot.crews.settings import crew_verbose
from template_multi_agent_chatbot.events import ConversationalEventBus
from template_multi_agent_chatbot.tools import (
    NanoBananaImageEditingTool,
    NanoBananaImageGenerationTool,
)

_IMAGE_SKILL_PATH = str(
    Path(__file__).resolve().parent.parent / "skills" / "image-generation"
)


class ImageCreationCrew:
    def __init__(
        self,
        messages: list[LLMMessage],
        event_bus: ConversationalEventBus,
        source: Any,
    ):
        self._messages = messages
        self._event_bus = event_bus
        self._source = source

    def _agent(self) -> Agent:
        return Agent(
            role="CrewAI Image Creation Assistant",
            goal="""Help users create and edit images based on their requests. Your text output is
streamed live to the user in real time — write as if speaking directly to them.

You specialize in understanding what the user wants visually and translating that into
precise prompts for image generation and editing tools. Before calling a tool, briefly
state what you are about to do.

ABSOLUTE RULE: NEVER include image references (image#1), file paths, filenames, or
storage locations in any message. The image is delivered automatically — just describe it.""",
            backstory="""You are a creative assistant with deep expertise in image generation and editing.

You excel at interpreting visual requests from conversation context, crafting effective
prompts for image generation models, and iterating on edits when the user wants changes.
CRITICAL: You must respond solely in the same language the user is using.
CRITICAL: You must NEVER reveal image references, filenames, or storage details.
Images are delivered automatically. Never say "here it is: image#1" or similar.""",
            llm=LLM(model="gemini/gemini-3.1-pro-preview", stream=True),
            skills=[_IMAGE_SKILL_PATH],
            tools=[
                NanoBananaImageGenerationTool(
                    event_bus=self._event_bus,
                    source=self._source,
                ),
                NanoBananaImageEditingTool(
                    event_bus=self._event_bus,
                    source=self._source,
                ),
            ],
            max_iter=8,
            allow_delegation=False,
        )

    def _task(self, agent: Agent) -> Task:
        return Task(
            description=f"""Handle the user's image creation or editing request based on the conversation history.
Generate or edit images as requested, and communicate clearly with the user throughout.

IMPORTANT: You must carefully read the conversation history to ensure you are not repeating
information, greetings, or phrases you have already used.

CONVERSATION HISTORY (last 10 messages):
{format_history(self._messages)}

CURRENT DATE AND TIME (UTC):
{utc_now()}
""",
            expected_output="""A helpful response to the user's image request. KEY RULES:
- Stay grounded in the conversation history; do not invent prior context
- Respond in the same language the user is using
- Before calling a tool, briefly state what you are about to do
- NEVER repeat the same content, phrasing, or greetings used in previous messages
- NEVER include image references (image#1), file paths, filenames, or storage locations.
  The image is delivered automatically. Just describe what you created.""",
            agent=agent,
        )

    def _crew(self) -> Crew:
        # One agent instance, shared by the crew roster and the task.
        agent = self._agent()
        return Crew(
            agents=[agent],
            tasks=[self._task(agent)],
            process=Process.sequential,
            verbose=crew_verbose(),
        )

    def execute(self) -> str:
        return self._crew().kickoff().raw
