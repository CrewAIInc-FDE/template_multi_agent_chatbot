import os
from pathlib import Path

from crewai import LLM, Agent, Crew, Process, Task
from crewai.utilities.types import LLMMessage

from template_multi_agent_chatbot.crews.history import format_history, utc_now
from template_multi_agent_chatbot.platform_health import warn_if_unavailable

_SLACK_SKILL_PATH = str(Path(__file__).resolve().parent.parent / "skills" / "slack")

# Read-only Slack actions. Safe to run against a live workspace: they surface
# information that the person chatting could already see.
_READ_ACTIONS = [
    "slack/search_messages",
    "slack/list_channels",
    "slack/list_members",
    "slack/get_user_by_email",
    "slack/get_users_by_name",
]

# Writes post into a real workspace as the shared org account, so they are
# opt-in. A demo that accidentally messages a live channel is worse than a demo
# that can only read.
_WRITE_ACTIONS = [
    "slack/send_message",
    "slack/send_direct_message",
]


def writes_enabled() -> bool:
    return os.getenv("SLACK_ALLOW_WRITES", "").strip().lower() in {"1", "true", "yes"}


def slack_apps() -> list[str]:
    return _READ_ACTIONS + (_WRITE_ACTIONS if writes_enabled() else [])


class SlackCrew:
    def __init__(self, messages: list[LLMMessage]):
        self._messages = messages

    def _agent(self) -> Agent:
        apps = slack_apps()
        # apps= resolves silently to nothing when the integration isn't
        # connected; surface that in the logs rather than letting the agent
        # improvise an excuse.
        warn_if_unavailable(apps)

        write_rules = (
            """You MAY send messages, but only when the user clearly asks you to.
Before sending, state exactly what you will post and where. Never send a message
the user did not ask for."""
            if writes_enabled()
            else """You are READ-ONLY. You cannot send messages or DMs. If the user asks
you to post something, explain that sending is disabled in this demo and offer
to draft the message text for them to send themselves."""
        )

        return Agent(
            role="Slack Workspace Assistant",
            goal=f"""Help the user find and understand what is happening in their Slack
workspace. Your text output is streamed live to the user in real time — write as if
speaking directly to them.

You search conversations, identify the right channels and people, and summarize
discussions accurately. Before calling a tool, briefly state what you are about to do.

{write_rules}""",
            backstory="""You are an expert at navigating busy Slack workspaces.

You know that search works best with distinctive keywords rather than long sentences,
that the right channel is often more useful than the right message, and that a summary
of a thread beats a wall of quoted text.

CRITICAL: Respond solely in the same language the user is using.
CRITICAL: Never invent messages, channels, or people. If a search returns nothing,
say so plainly and suggest a different search term. Attribute quotes to the person
who actually wrote them, and say when a discussion is inconclusive rather than
manufacturing a resolution.""",
            llm=LLM(model="gemini/gemini-3.1-pro-preview", stream=True),
            skills=[_SLACK_SKILL_PATH],
            apps=apps,
            max_iter=8,
            allow_delegation=False,
        )

    def _task(self, agent: Agent) -> Task:
        return Task(
            description=f"""Answer the user's question about their Slack workspace using the
Slack tools available to you.

Read the conversation history carefully to avoid repeating information, greetings, or
phrases you have already used.

CONVERSATION HISTORY (last 10 messages):
{format_history(self._messages)}

CURRENT DATE AND TIME (UTC):
{utc_now()}

KEY RULES:
- Before calling a tool, briefly state what you are about to do.
- Search with distinctive keywords, not whole sentences. Refine and retry if the
  first search is unhelpful.
- Attribute what you find to the people who said it, and link context together
  rather than dumping raw results.
- Never fabricate messages, channels, or usernames.
- Respond in the same language the user is using.
""",
            expected_output="A clear answer grounded in what the Slack tools actually "
            "returned, naming the relevant channels and people, in the user's language.",
            agent=agent,
        )

    def _crew(self) -> Crew:
        agent = self._agent()
        return Crew(
            agents=[agent],
            tasks=[self._task(agent)],
            process=Process.sequential,
            verbose=True,
        )

    def execute(self) -> str:
        return self._crew().kickoff().raw
