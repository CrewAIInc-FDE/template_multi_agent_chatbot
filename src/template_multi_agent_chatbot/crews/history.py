from datetime import datetime, timezone

from crewai.utilities.types import LLMMessage

HISTORY_WINDOW = 10


def format_history(messages: list[LLMMessage], limit: int = HISTORY_WINDOW) -> str:
    """Render recent conversation history for a task description.

    Messages arrive as LLM-shaped dicts from `Flow.conversation_messages`.
    `tool` messages carry generated image paths, which is how the image crew
    knows what an edit request refers to.
    """
    return "\n".join(
        f"[{message.get('role', 'user').upper()}] {str(message.get('content', '')).strip()}"
        for message in messages[-limit:]
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")
