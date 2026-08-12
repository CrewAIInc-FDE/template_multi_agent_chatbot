from pathlib import Path

from crewai import LLM, Agent, Crew, Process, Task
from crewai.utilities.types import LLMMessage
from crewai_tools import ScrapeWebsiteTool, SerperDevTool

from template_multi_agent_chatbot.crews.history import format_history, utc_now
from template_multi_agent_chatbot.crews.settings import crew_verbose

_SEARCH_SKILL_PATH = str(
    Path(__file__).resolve().parent.parent / "skills" / "internet-searching"
)


class InternetSearchCrew:
    def __init__(self, messages: list[LLMMessage]):
        self._messages = messages

    def _agent(self) -> Agent:
        return Agent(
            role="CrewAI Internet Research Assistant",
            goal="""Help users find information on the internet by searching and scraping websites.
Your text output is streamed live to the user in real time — write as if speaking directly
to them.

You specialize in formulating effective search queries, evaluating sources, and synthesizing
findings into clear, accurate answers. Before calling a tool, briefly state what you are
about to do.""",
            backstory="""You are a skilled research assistant with expertise in web search and information
synthesis.

You excel at breaking down complex questions into targeted search queries, identifying
reliable sources, and distilling large amounts of information into concise, useful answers.
You always cite your sources and communicate transparently about your research process.
CRITICAL: Respond solely in the same language the user is using.

CITATION RULES:
Any answer based on web search or scraping MUST end with a list of source URLs you
actually consulted. Never omit sources and never fabricate a URL you didn't visit.""",
            llm=LLM(model="gemini/gemini-3.1-pro-preview", stream=True),
            skills=[_SEARCH_SKILL_PATH],
            tools=[
                SerperDevTool(),
                ScrapeWebsiteTool(),
            ],
            max_iter=8,
            allow_delegation=False,
        )

    def _task(self, agent: Agent) -> Task:
        return Task(
            description=f"""Research the user's question using internet search and web scraping as needed.
Provide a thorough, well-sourced answer based on the conversation history.

Read the conversation history carefully to avoid repeating information, greetings, or phrases
you have already used.

CONVERSATION HISTORY (last 10 messages):
{format_history(self._messages)}

CURRENT DATE AND TIME (UTC):
{utc_now()}

KEY RULES:
- Before calling a tool, briefly state what you are about to do.
- Never repeat the same content across messages. Each message must carry new information.
- Stay grounded in the conversation history; do not invent prior context.
- Respond in the same language the user is using.
""",
            expected_output="A thorough answer to the user's question written in their language, "
            "with source citations appended as a URL list.",
            agent=agent,
        )

    def _crew(self) -> Crew:
        # One agent instance, shared by the crew roster and the task — building it
        # twice leaves `agents=[...]` and `task.agent` as different objects.
        agent = self._agent()
        return Crew(
            agents=[agent],
            tasks=[self._task(agent)],
            process=Process.sequential,
            verbose=crew_verbose(),
        )

    def execute(self) -> str:
        return self._crew().kickoff().raw
