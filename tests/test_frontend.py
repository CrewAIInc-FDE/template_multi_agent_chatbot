"""Flask UI: auth gate, rate limiting, and the missing-config path.

`app.py` reads configuration at import time, so each scenario reloads the module
with the environment it needs.
"""

import importlib

import pytest

PASSWORD = "test-password"

BASE_ENV = {
    "DEPLOYMENT_URL": "https://example.invalid",
    "DEPLOYMENT_KEY": "deployment-key",
    "WEBHOOK_TOKEN": "webhook-token",
    "APP_PASSWORD": PASSWORD,
    "RATE_LIMIT_MESSAGES": "3",
    "RATE_LIMIT_WINDOW": "600",
    "DAILY_MESSAGE_CAP": "100",
}


def _load_app(monkeypatch, **overrides):
    for key, value in {**BASE_ENV, **overrides}.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    import app as app_module

    reloaded = importlib.reload(app_module)
    reloaded.app.config["TESTING"] = True
    return reloaded


@pytest.fixture
def client(monkeypatch):
    module = _load_app(monkeypatch)
    return module.app.test_client()


def _sign_in(client, password=PASSWORD):
    return client.post("/login", data={"password": password})


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_pages_redirect_to_login_when_signed_out(client):
    response = client.get("/")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_api_returns_401_rather_than_a_redirect(client):
    """The UI parses JSON; an HTML redirect would surface as a parse error."""
    assert client.get("/api/channels").status_code == 401


def test_wrong_password_is_rejected(client):
    assert _sign_in(client, "not-the-password").status_code == 401
    assert client.get("/api/channels").status_code == 401


def test_correct_password_grants_access(client):
    assert _sign_in(client).status_code == 302
    assert client.get("/api/channels").status_code == 200


def test_logout_revokes_access(client):
    _sign_in(client)
    client.get("/logout")

    assert client.get("/api/channels").status_code == 401


def test_webhook_is_exempt_from_the_session_gate(client):
    """AMP calls this with a bearer token and no cookie. Gating it on the session
    would silently break every reply."""
    unauthorized = client.post(
        "/api/webhook/unknown", json={}, headers={"Authorization": "Bearer wrong"}
    )
    authorized = client.post(
        "/api/webhook/unknown",
        json={},
        headers={"Authorization": f"Bearer {BASE_ENV['WEBHOOK_TOKEN']}"},
    )

    assert unauthorized.status_code == 401  # from the token check, not a redirect
    assert authorized.status_code == 200


def test_auth_is_disabled_when_no_password_is_set(monkeypatch):
    """A fresh clone should run locally without ceremony."""
    module = _load_app(monkeypatch, APP_PASSWORD=None)

    assert module.app.test_client().get("/api/channels").status_code == 200


def test_next_parameter_cannot_redirect_off_site(client):
    response = client.post(f"/login?next=//evil.example", data={"password": PASSWORD})

    assert response.headers["Location"] == "/"


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def _send(client, channel_id, content="hello"):
    return client.post(f"/api/channels/{channel_id}/messages", json={"content": content})


@pytest.fixture
def channel(client):
    _sign_in(client)
    created = client.post("/api/channels", json={"name": "test"}).get_json()
    yield created["id"]
    client.delete(f"/api/channels/{created['id']}")


def test_sends_are_allowed_up_to_the_limit(client, channel):
    for _ in range(int(BASE_ENV["RATE_LIMIT_MESSAGES"])):
        assert _send(client, channel).status_code == 202


def test_exceeding_the_limit_returns_429(client, channel):
    for _ in range(int(BASE_ENV["RATE_LIMIT_MESSAGES"])):
        _send(client, channel)

    response = _send(client, channel)

    assert response.status_code == 429
    assert "Rate limit reached" in response.get_json()["error"]


def test_daily_cap_is_enforced(monkeypatch):
    module = _load_app(monkeypatch, DAILY_MESSAGE_CAP="1", RATE_LIMIT_MESSAGES="100")
    client = module.app.test_client()
    _sign_in(client)
    channel_id = client.post("/api/channels", json={"name": "cap"}).get_json()["id"]

    first = _send(client, channel_id)
    second = _send(client, channel_id)
    client.delete(f"/api/channels/{channel_id}")

    assert first.status_code == 202
    assert second.status_code == 429
    assert "Daily limit reached" in second.get_json()["error"]


# ---------------------------------------------------------------------------
# Missing deployment config
# ---------------------------------------------------------------------------


def test_missing_deployment_config_names_the_variables(monkeypatch):
    """Without this the empty URL surfaces as requests' "Invalid URL '/kickoff'",
    which gives no hint that the real problem is unset configuration."""
    module = _load_app(monkeypatch, DEPLOYMENT_URL=None, DEPLOYMENT_KEY=None)
    client = module.app.test_client()
    _sign_in(client)
    channel_id = client.post("/api/channels", json={"name": "cfg"}).get_json()["id"]

    response = _send(client, channel_id)
    client.delete(f"/api/channels/{channel_id}")

    assert response.status_code == 503
    error = response.get_json()["error"]
    assert "DEPLOYMENT_URL" in error and "DEPLOYMENT_KEY" in error
