import os

import requests
from crewai.events.base_events import BaseEvent


class Dispatcher:
    """Posts events straight to the UI's per-channel webhook endpoint.

    AMP relays most events automatically, but not llm_thinking_chunk (and
    custom events arrive inconsistently), so the flow pushes those directly.
    The target URL is per-conversation and carried in from the kickoff inputs;
    the bearer token is the shared WEBHOOK_TOKEN the UI validates."""

    def __init__(self, url: str):
        self._url = url
        self._headers = {
            "Authorization": f"Bearer {os.environ.get('WEBHOOK_TOKEN', '')}",
            "Content-Type": "application/json",
        }

    def dispatch(self, event: BaseEvent):
        payload = {"type": event.type, "data": event.to_json()}
        try:
            response = requests.post(
                self._url,
                headers=self._headers,
                json=payload,
                timeout=10,
            )
            response.raise_for_status()
        except Exception as e:
            print(f"Error dispatching event to {self._url}: {e}")
