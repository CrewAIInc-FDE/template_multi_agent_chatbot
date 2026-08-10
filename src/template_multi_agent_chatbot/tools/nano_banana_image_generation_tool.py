import base64
from typing import Any, ClassVar, Type

from crewai.tools import BaseTool
from google import genai
from pydantic import BaseModel, ConfigDict, Field

from template_multi_agent_chatbot.events.conversational_event_bus import (
    ConversationalEventBus,
)


class NanoBananaImageGenerationToolInput(BaseModel):
    prompt: str = Field(..., description="The prompt to generate the image.")


class NanoBananaImageGenerationTool(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = "Nano Banana Image Generation"
    description: str = (
        "Generate an image through Nano Banana based on a user prompt. "
        "Returns an image reference like 'image#1' that the editing tool can "
        "use later to modify this image."
    )
    args_schema: Type[BaseModel] = NanoBananaImageGenerationToolInput

    event_bus: ConversationalEventBus
    source: Any

    client: ClassVar[genai.Client] = genai.Client()

    def _run(self, prompt: str) -> dict:
        response = self.client.models.generate_content(
            model="gemini-3.1-flash-image",
            contents=[prompt],
        )

        # Scan every part: the model routinely emits a text part before the image
        # one, so returning on the first non-image part reports a false failure.
        for part in response.parts:
            if part.inline_data is None:
                if part.text is not None:
                    print(part.text)
                continue

            image_base64 = base64.b64encode(part.inline_data.data).decode("utf-8")
            reference = self.event_bus.store_image(image_base64)
            self.event_bus.append_tool_message(f"Generated {reference}")
            self.event_bus.emit_image_generated(self.source, image_base64)

            return {
                "output": "Image generated successfully.",
                "image_reference": reference,
            }

        return {"output": "Failed to generate image."}
