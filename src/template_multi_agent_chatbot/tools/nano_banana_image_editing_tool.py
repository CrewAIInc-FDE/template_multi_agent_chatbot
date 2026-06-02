import base64
from datetime import datetime
from typing import Any, ClassVar, Type

from crewai.tools import BaseTool
from google import genai
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from template_multi_agent_chatbot.events.conversational_event_bus import (
    ConversationalEventBus,
)
from template_multi_agent_chatbot.types import Message


class NanoBananaImageEditingToolInput(BaseModel):
    prompt: str = Field(
        ...,
        description="Instructions describing how to edit the image (e.g. 'add sunglasses', 'change background to a beach').",
    )
    image_path: str = Field(
        ...,
        description="Path to the source image to edit (e.g. '/tmp/140110.png').",
    )


class NanoBananaImageEditingTool(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = "Nano Banana Image Editing"
    description: str = (
        "Edit an existing image through Nano Banana based on a text prompt. "
        "Provide the path to the source image and a description of the desired edits."
    )
    args_schema: Type[BaseModel] = NanoBananaImageEditingToolInput

    event_bus: ConversationalEventBus
    source: Any

    client: ClassVar[genai.Client] = genai.Client()

    def _run(self, prompt: str, image_path: str) -> dict:
        try:
            source_image = Image.open(image_path)
        except FileNotFoundError:
            return {"output": f"Source image not found at '{image_path}'."}

        response = self.client.models.generate_content(
            model="gemini-3.1-flash-image",
            contents=[prompt, source_image],
        )
        filename = datetime.now().strftime("%H%M%S")

        for part in response.parts:
            if part.text is not None:
                print(part.text)
            elif part.inline_data is not None:
                image = part.as_image()
                image.save(f"/tmp/{filename}.png")

                image_bytes = open(f"/tmp/{filename}.png", "rb").read()
                image_base64 = base64.b64encode(image_bytes).decode("utf-8")
                self.event_bus.append_message(
                    Message.create(role="tool", content=f"/tmp/{filename}.png"),
                )
                self.event_bus.emit_image_generated(self.source, image_base64)

                return {
                    "output": "Image edited successfully.",
                    "filename": f"/tmp/{filename}.png",
                }

            return {"output": "Failed to edit image."}

        return {"output": "Failed to edit image."}
