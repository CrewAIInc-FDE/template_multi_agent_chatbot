from pathlib import Path

from crewai import LLM, Agent, Crew, Process, Task
from crewai.utilities.types import LLMMessage

from template_multi_agent_chatbot.crews.history import format_history, utc_now
from template_multi_agent_chatbot.crews.settings import crew_verbose, load_skill
from template_multi_agent_chatbot import platform_token_shim, user_context
from template_multi_agent_chatbot.platform_health import warn_if_unavailable


# Read-only Slack actions — the complete set this agent may use.
#
# The allowlist IS the safety boundary. `apps=["slack"]` would hand the agent all
# 167 actions the integration exposes, including SLACK_ARCHIVE_CONVERSATION,
# SLACK_DELETES_A_MESSAGE_FROM_A_CHAT, SLACK_SEND_MESSAGE and enterprise user
# management. Naming actions individually is what keeps an assistant pointed at a
# live workspace from being able to change it.
#
# Every name here is verified against the live /actions endpoint, NOT taken from
# the integration docs — five of the seven names those docs list resolve to
# nothing, and an unrecognised reference is dropped silently rather than raising.
# Check any addition with `platform_health.unresolved_actions()` first, and keep
# the list tight: tool-selection quality degrades as the list grows.
READ_ONLY_ACTIONS = [
    # Finding things
    "slack/search_messages",
    "slack/find_channels",
    "slack/find_users",
    "slack/list_all_channels",
    # Reading conversations
    "slack/fetch_conversation_history",
    "slack/fetch_message_thread_from_a_conversation",
    "slack/retrieve_conversation_information",
    "slack/retrieve_conversation_members_list",
    # People and citations
    "slack/retrieve_detailed_user_information",
    "slack/retrieve_message_permalink_url",
]


def slack_apps() -> list[str]:
    """Actions this agent is permitted to use. Read-only, by construction."""
    return list(READ_ONLY_ACTIONS)


class SlackCrew:
    def __init__(self, messages: list[LLMMessage]):
        self._messages = messages

    def _agent(self) -> Agent:
        apps = slack_apps()
        # apps= resolves silently to nothing when the integration isn't
        # connected; surface that in the logs rather than letting the agent
        # improvise an excuse.
        warn_if_unavailable(apps)

        # Lets these tools use the chatting user's own CrewAI integration token
        # when they have one, instead of the shared org connection. Without a
        # user token this is a no-op and behaviour is unchanged.
        platform_token_shim.apply()

        return Agent(
            role="Slack Workspace Assistant",
            goal="""Help the user find and understand what is happening in their Slack
workspace. Your text output is streamed live to the user in real time — write as if
speaking directly to them.

You search conversations, identify the right channels and people, and summarize
discussions accurately. Before calling a tool, briefly state what you are about to do.

You are READ-ONLY: you can search and read, but you cannot post, reply, react, pin,
or change anything. If the user asks you to send a message, say plainly that you can
only read Slack, then offer to draft the text for them to send themselves.""",
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
            skills=[load_skill("slack")],
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
            verbose=crew_verbose(),
        )

    def execute(self) -> str:
        return self._crew().kickoff().raw
