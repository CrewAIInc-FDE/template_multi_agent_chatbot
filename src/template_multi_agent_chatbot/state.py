from crewai.experimental.conversational import ConversationState
from pydantic import Field

# Images are held in flow state rather than on disk, so an edit works even when
# the next turn lands in a different AMP container. Base64 PNGs are large, so
# only the most recent few are kept.
MAX_STORED_IMAGES = 4


class ChatbotState(ConversationState):
    """Conversation state plus the images generated during this session.

    `images` maps a short reference (`image#1`) to base64 PNG data. Only the
    reference ever reaches the transcript — putting base64 in `messages` would
    balloon every downstream prompt, since the crews render history into their
    task descriptions.
    """

    images: dict[str, str] = Field(default_factory=dict)
    image_counter: int = 0

    def store_image(self, image_base64: str) -> str:
        """Store an image and return the reference the agent should use."""
        self.image_counter += 1
        reference = f"image#{self.image_counter}"
        self.images[reference] = image_base64

        # Monotonic counter, so eviction can never recycle a reference that's
        # still sitting in the transcript.
        while len(self.images) > MAX_STORED_IMAGES:
            self.images.pop(next(iter(self.images)))

        return reference

    def get_image(self, reference: str) -> str | None:
        return self.images.get(reference)
