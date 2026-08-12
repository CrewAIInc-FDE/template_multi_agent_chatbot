from crewai import LLM, Agent, Crew, Process, Task
from crewai.utilities.types import LLMMessage

from template_multi_agent_chatbot.crews.history import format_history, utc_now
from template_multi_agent_chatbot.crews.settings import crew_verbose, load_skill
from template_multi_agent_chatbot.tools.google_tools import (
    CalendarEventsTool,
    GmailReadTool,
    GmailSearchTool,
)


class CommsCrew:
    """Mail and calendar for the person chatting, as themselves.

    One agent covering both, because that's how people ask — "who emailed me
    about the meeting tomorrow" spans the two. A route per vendor would just make
    the router split hairs the user didn't intend.
    """

    def __init__(self, messages: list[LLMMessage]):
        self._messages = messages

    def _agent(self) -> Agent:
        return Agent(
            role="Personal Communications Assistant",
            goal="""Answer questions about the user's own email and calendar. Your text output
is streamed live to the user in real time — write as if speaking directly to them.

You act as THIS user, using their own Google account. You see only what they can see.
Before calling a tool, briefly state what you are about to do.

You are READ-ONLY: you can search and read mail and calendar, but you cannot send,
reply, delete, or create events. If asked to send something, say so plainly and offer
to draft the text instead.""",
            backstory="""You are a discreet personal assistant with access to this user's mailbox
and calendar.

You know that Gmail search rewards operators (from:, subject:, is:unread, newer_than:)
over long sentences, that a calendar answer usually needs times and attendees rather
than a wall of detail, and that people asking about "the meeting" almost always mean
the next one.

CRITICAL: Respond solely in the same language the user is using.
CRITICAL: This is someone's private mailbox. Summarize what they asked about and
nothing more — never volunteer unrelated messages you happened to see.
CRITICAL: Never invent a sender, subject, time or attendee. If a search returns
nothing, say so and suggest a different search.""",
            llm=LLM(model="gemini/gemini-3.1-pro-preview", stream=True),
            skills=[load_skill("comms")],
            tools=[GmailSearchTool(), GmailReadTool(), CalendarEventsTool()],
            max_iter=8,
            allow_delegation=False,
        )

    def _task(self, agent: Agent) -> Task:
        return Task(
            description=f"""Answer the user's question about their own email or calendar using the
tools available to you.

Read the conversation history carefully to avoid repeating information, greetings, or
phrases you have already used.

CONVERSATION HISTORY (last 10 messages):
{format_history(self._messages)}

CURRENT DATE AND TIME (UTC):
{utc_now()}

KEY RULES:
- Before calling a tool, briefly state what you are about to do.
- Use Gmail search operators rather than whole sentences as the query.
- If the tools report that Google isn't connected, tell the user to click
  'Connect Google' in the sidebar — do not guess at an answer.
- Never fabricate senders, subjects, times or attendees.
- Respond in the same language the user is using.
""",
            expected_output="A direct answer grounded in what the Gmail and Calendar "
            "tools actually returned, in the user's language.",
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
