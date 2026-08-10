import base64
import io
from typing import Any, ClassVar, Type

from crewai.tools import BaseTool
from google import genai
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from template_multi_agent_chatbot.events.conversational_event_bus import (
    ConversationalEventBus,
)


class NanoBananaImageEditingToolInput(BaseModel):
    prompt: str = Field(
        ...,
        description="Instructions describing how to edit the image (e.g. 'add sunglasses', 'change background to a beach').",
    )
    image_reference: str = Field(
        ...,
        description="Reference of the image to edit, as returned by the generation tool (e.g. 'image#1').",
    )


class NanoBananaImageEditingTool(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = "Nano Banana Image Editing"
    description: str = (
        "Edit an image generated earlier in this conversation. Provide the image "
        "reference (e.g. 'image#1') and a description of the desired edits. "
        "Returns a new reference for the edited image."
    )
    args_schema: Type[BaseModel] = NanoBananaImageEditingToolInput

    event_bus: ConversationalEventBus
    source: Any

    client: ClassVar[genai.Client] = genai.Client()

    def _run(self, prompt: str, image_reference: str) -> dict:
        stored = self.event_bus.get_image(image_reference.strip())
        if stored is None:
            return {
                "output": (
                    f"No image found for '{image_reference}'. Check the conversation "
                    "for a reference like 'image#1', or generate a new image first."
                )
            }

        source_image = Image.open(io.BytesIO(base64.b64decode(stored)))

        response = self.client.models.generate_content(
            model="gemini-3.1-flash-image",
            contents=[prompt, source_image],
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
            self.event_bus.append_tool_message(
                f"Edited {image_reference} into {reference}"
            )
            self.event_bus.emit_image_generated(self.source, image_base64)

            return {
                "output": "Image edited successfully.",
                "image_reference": reference,
            }

        return {"output": "Failed to edit image."}
