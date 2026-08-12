"""Gmail and Calendar tools that act as the signed-in user.

Every call reads the chatting user's OAuth token from `user_context` at
execution time — not at construction — so one agent instance serves different
users correctly and a token refreshed mid-session is picked up.

These are hand-written rather than routed through CrewAI's `apps=`/MCP
integrations for one reason: both of those resolve credentials from
`CREWAI_PLATFORM_INTEGRATION_TOKEN` via `os.getenv` and cannot be scoped to a
person. Reading the token here is what makes "acts as you" true.
"""

import base64
from datetime import datetime, timedelta, timezone
from typing import Any, Type

import requests
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from template_multi_agent_chatbot import user_context

_GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
_CALENDAR = "https://www.googleapis.com/calendar/v3"

_NOT_CONNECTED = (
    "The user hasn't connected their Google account yet. Tell them to click "
    "'Connect Google' in the sidebar to grant mail and calendar access — you "
    "cannot see anything until they do."
)


def _google_get(path: str, params: dict[str, Any]) -> dict | str:
    """GET a Google endpoint as the signed-in user.

    Returns the parsed body, or a plain-English string the agent should relay.
    """
    token = user_context.access_token_for("google")
    if not token:
        return _NOT_CONNECTED

    try:
        response = requests.get(
            path,
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=30,
        )
    except Exception as exc:
        return f"Google request failed: {exc}"

    if response.status_code in (401, 403):
        # The grant exists but is stale or lacks the scope. Say which, because
        # "reconnect" and "grant more access" are different user actions.
        return (
            "Google rejected the request — the user's access has expired or "
            "doesn't cover this. Ask them to reconnect Google from the sidebar."
        )
    if not response.ok:
        return f"Google returned {response.status_code}: {response.text[:200]}"

    return response.json()


def _header(payload: dict, name: str) -> str:
    for header in payload.get("payload", {}).get("headers", []):
        if header.get("name", "").lower() == name.lower():
            return header.get("value", "")
    return ""


class GmailSearchInput(BaseModel):
    query: str = Field(
        ...,
        description=(
            "Gmail search query, same syntax as the Gmail search box. "
            "Examples: 'from:sarah invoice', 'is:unread newer_than:7d', "
            "'subject:renewal has:attachment'."
        ),
    )
    max_results: int = Field(10, description="How many messages to return (1-25).")


class GmailSearchTool(BaseTool):
    name: str = "Search Gmail"
    description: str = (
        "Search the signed-in user's own Gmail and return matching messages with "
        "sender, subject, date and a snippet. Read-only."
    )
    args_schema: Type[BaseModel] = GmailSearchInput

    def _run(self, query: str, max_results: int = 10) -> str:
        listing = _google_get(
            f"{_GMAIL}/messages",
            {"q": query, "maxResults": max(1, min(max_results, 25))},
        )
        if isinstance(listing, str):
            return listing

        ids = [m["id"] for m in listing.get("messages", [])]
        if not ids:
            return f"No messages matched {query!r}."

        results = []
        for message_id in ids:
            detail = _google_get(
                f"{_GMAIL}/messages/{message_id}",
                {
                    "format": "metadata",
                    "metadataHeaders": ["From", "Subject", "Date"],
                },
            )
            if isinstance(detail, str):
                continue
            results.append(
                {
                    "id": message_id,
                    "from": _header(detail, "From"),
                    "subject": _header(detail, "Subject"),
                    "date": _header(detail, "Date"),
                    "snippet": detail.get("snippet", ""),
                }
            )

        lines = [
            f"- [{r['id']}] {r['date']} | {r['from']} | {r['subject']}\n  {r['snippet']}"
            for r in results
        ]
        return f"{len(results)} message(s) for {query!r}:\n" + "\n".join(lines)


class GmailReadInput(BaseModel):
    message_id: str = Field(..., description="Message id returned by Search Gmail.")


class GmailReadTool(BaseTool):
    name: str = "Read Gmail Message"
    description: str = (
        "Read the full body of one of the signed-in user's emails, by the id "
        "returned from Search Gmail. Read-only."
    )
    args_schema: Type[BaseModel] = GmailReadInput

    def _run(self, message_id: str) -> str:
        detail = _google_get(f"{_GMAIL}/messages/{message_id}", {"format": "full"})
        if isinstance(detail, str):
            return detail

        def extract(part: dict) -> str:
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                raw = part["body"]["data"].replace("-", "+").replace("_", "/")
                return base64.b64decode(raw + "==").decode("utf-8", "replace")
            return "".join(extract(p) for p in part.get("parts", []) or [])

        body = extract(detail.get("payload", {})) or detail.get("snippet", "")
        return (
            f"From: {_header(detail, 'From')}\n"
            f"Subject: {_header(detail, 'Subject')}\n"
            f"Date: {_header(detail, 'Date')}\n\n"
            f"{body[:4000]}"
        )


class CalendarInput(BaseModel):
    days_ahead: int = Field(7, description="How many days forward to look (1-60).")
    query: str | None = Field(
        None, description="Optional text to match in event titles or descriptions."
    )


class CalendarEventsTool(BaseTool):
    name: str = "List Calendar Events"
    description: str = (
        "List the signed-in user's own upcoming calendar events, with times and "
        "attendees. Read-only."
    )
    args_schema: Type[BaseModel] = CalendarInput

    def _run(self, days_ahead: int = 7, query: str | None = None) -> str:
        now = datetime.now(timezone.utc)
        params = {
            "timeMin": now.isoformat(),
            "timeMax": (now + timedelta(days=max(1, min(days_ahead, 60)))).isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": 25,
        }
        if query:
            params["q"] = query

        data = _google_get(f"{_CALENDAR}/calendars/primary/events", params)
        if isinstance(data, str):
            return data

        events = data.get("items", [])
        if not events:
            return f"No events in the next {days_ahead} day(s)."

        lines = []
        for event in events:
            start = event.get("start", {})
            when = start.get("dateTime") or start.get("date") or "?"
            attendees = ", ".join(
                a.get("email", "") for a in event.get("attendees", [])[:6]
            )
            lines.append(
                f"- {when} | {event.get('summary', '(no title)')}"
                + (f" | with {attendees}" if attendees else "")
            )
        return f"{len(events)} event(s) in the next {days_ahead} day(s):\n" + "\n".join(
            lines
        )
