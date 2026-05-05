import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from crewai import LLM, Agent, Crew, Process, Task
from crewai_tools import MongoDBVectorSearchConfig
from pydantic import BaseModel

from template_multi_agent_chatbot.tools import TrackedMongoDBVectorSearchTool
from template_multi_agent_chatbot.types import Message


class CrewExecutionResult(BaseModel):
    user_message: str
    query_chunks: list[dict[str, list[dict[str, Any]]]]
    agent_response: str


_DOCS_SKILL_PATH = str(
    Path(__file__).resolve().parent.parent / "skills" / "crewai-docs"
)
_NEWLINE = "\n"


class CrewaiDocsCrew:
    def __init__(self, user_message: Message, messages: list[Message]):
        self._user_message = user_message
        self._messages = messages
        self._query_chunks: list[dict[str, list[dict[str, Any]]]] = []

    def _agent(self) -> Agent:
        return Agent(
            role="CrewAI Documentation Expert",
            goal="""Help users with questions about the CrewAI framework by searching the official
documentation stored in a vector database. Your text output is streamed live to the user
in real time — write as if speaking directly to them.

You specialize in retrieving and synthesizing information from the CrewAI docs to provide
accurate, detailed answers about agents, tasks, crews, flows, tools, deployments, and every
other aspect of the framework. Before calling a tool, briefly state what you are about to do.""",
            backstory="""You are an expert on the CrewAI framework with access to the full official documentation
via vector search. You excel at translating user questions into effective search queries,
finding the most relevant documentation sections, and crafting clear, practical answers
with code examples when appropriate.

CRITICAL: Respond solely in the same language the user is using.
When the docs contain code examples, include them in your answer — they are very
valuable to the user.
If the vector search returns no relevant results, say so honestly and suggest the user
check the official docs at https://docs.crewai.com.""",
            llm=LLM(model="gemini/gemini-3.1-pro-preview", stream=True),
            skills=[_DOCS_SKILL_PATH],
            tools=[
                TrackedMongoDBVectorSearchTool(
                    query_chunks=self._query_chunks,
                    connection_string=os.environ["MONGODB_CONNECTION_STRING"],
                    database_name=os.environ["MONGODB_DATABASE_NAME"],
                    collection_name=os.environ["MONGODB_COLLECTION_NAME"],
                    dimensions=3072,
                    query_config=MongoDBVectorSearchConfig(limit=10),
                ),
            ],
        )

    def _task(self) -> Task:
        return Task(
            description=f"""Answer the user's question about CrewAI by searching the official documentation
via the MongoDB vector search tool. Provide a thorough, accurate answer based on the docs.

Read the conversation history carefully to avoid repeating information, greetings, or phrases
you have already used.

CONVERSATION HISTORY (last 10 messages):
{_NEWLINE.join([f"[{msg.role.upper()}] {msg.content.strip()}" for msg in self._messages[-10:]])}

CURRENT DATE AND TIME (UTC):
{datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")}

KEY RULES:
- Before calling a tool, briefly state what you are about to do.
- Include code examples from the docs when they are relevant to the user's question.
- Never repeat the same content across messages. Each message must carry new information.
- Stay grounded in the conversation history; do not invent prior context.
- Respond in the same language the user is using.
""",
            expected_output="A thorough answer to the user's CrewAI question written in their language, "
            "grounded in the official CrewAI documentation, with code examples where relevant.",
            agent=self._agent(),
        )

    def _crew(self) -> Crew:
        return Crew(
            agents=[self._agent()],
            tasks=[self._task()],
            process=Process.sequential,
            verbose=True,
        )

    def execute(self) -> CrewExecutionResult:
        raw = self._crew().kickoff().raw
        return CrewExecutionResult(
            user_message=self._user_message.content,
            query_chunks=self._query_chunks,
            agent_response=raw,
        )
