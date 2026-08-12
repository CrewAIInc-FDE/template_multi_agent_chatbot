"""Per-user credentials: identity reaches tools, secrets don't reach state."""

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

SHARED_SECRET = "shared-secret"


@pytest.fixture(scope="module")
def credentials_server():
    """Stand-in for the UI's /api/internal/credentials/<user_id>."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get("Authorization") != f"Bearer {SHARED_SECRET}":
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b"{}")
                return
            user_id = self.path.rstrip("/").split("/")[-1]
            body = json.dumps(
                {
                    "user_id": user_id,
                    "providers": {
                        "google": {"access_token": f"token-{user_id}", "scopes": "x"}
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/api/internal/credentials"
    server.shutdown()


def test_credentials_load_into_context(monkeypatch, credentials_server):
    monkeypatch.setenv("WEBHOOK_TOKEN", SHARED_SECRET)
    from template_multi_agent_chatbot import user_context

    user_context.load_for_turn("user-a", credentials_server)

    assert user_context.current_user_id() == "user-a"
    assert user_context.access_token_for("google") == "token-user-a"
    assert user_context.connected_providers() == ["google"]


def test_each_user_gets_their_own_token(monkeypatch, credentials_server):
    monkeypatch.setenv("WEBHOOK_TOKEN", SHARED_SECRET)
    from template_multi_agent_chatbot import user_context

    user_context.load_for_turn("user-a", credentials_server)
    first = user_context.access_token_for("google")
    user_context.load_for_turn("user-b", credentials_server)

    assert first == "token-user-a"
    assert user_context.access_token_for("google") == "token-user-b"


def test_a_wrong_shared_secret_yields_no_credentials(monkeypatch, credentials_server):
    """Degrade to no credentials rather than killing the turn — the tools report
    the absence to the user themselves."""
    monkeypatch.setenv("WEBHOOK_TOKEN", "wrong")
    from template_multi_agent_chatbot import user_context

    user_context.load_for_turn("user-a", credentials_server)

    assert user_context.access_token_for("google") is None


def test_anonymous_turn_has_no_credentials(monkeypatch):
    from template_multi_agent_chatbot import user_context

    user_context.load_for_turn(None, None)

    assert user_context.current_user_id() is None
    assert user_context.connected_providers() == []


def test_unconnected_provider_returns_none(monkeypatch, credentials_server):
    monkeypatch.setenv("WEBHOOK_TOKEN", SHARED_SECRET)
    from template_multi_agent_chatbot import user_context

    user_context.load_for_turn("user-a", credentials_server)

    assert user_context.access_token_for("slack") is None


def test_kickoff_signature_declares_restore_from_state_id():
    """AMP introspects this signature. Hiding the parameter behind **kwargs made
    its chat API fail every message with a spurious 'upgrade crewAI' error."""
    import inspect

    from template_multi_agent_chatbot.main import ConversationalFlow

    params = inspect.signature(ConversationalFlow.kickoff).parameters
    assert "restore_from_state_id" in params
    assert "input_files" in params
    assert "from_checkpoint" in params
