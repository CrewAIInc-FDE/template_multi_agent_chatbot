from pydantic import BaseModel

from template_multi_agent_chatbot.types.message import Message


class ConversationalState(BaseModel):
    user_message: Message | None = None
    messages: list[Message] = []
    webhook_url: str | None = None
