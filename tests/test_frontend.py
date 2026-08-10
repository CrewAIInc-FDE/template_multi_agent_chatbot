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


# ---------------------------------------------------------------------------
# Google SSO
# ---------------------------------------------------------------------------

GOOGLE_ENV = {
    "GOOGLE_CLIENT_ID": "client-id",
    "GOOGLE_CLIENT_SECRET": "client-secret",
    "ALLOWED_EMAIL_DOMAINS": "crewai.com",
    "PUBLIC_BASE_URL": "https://demo.example",
    "APP_PASSWORD": None,
}


@pytest.fixture
def sso(monkeypatch):
    """App with Google SSO configured instead of a shared password."""
    import auth as auth_module

    for key, value in GOOGLE_ENV.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    importlib.reload(auth_module)
    module = _load_app(monkeypatch, **GOOGLE_ENV)
    return module


def test_sso_takes_precedence_over_the_password_form(sso):
    body = sso.app.test_client().get("/login").get_data(as_text=True)

    assert "Continue with Google" in body
    assert 'type="password"' not in body


def test_login_redirects_to_google_with_state(sso):
    client = sso.app.test_client()

    response = client.get("/auth/google")

    assert response.status_code == 302
    location = response.headers["Location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth")
    assert "client_id=client-id" in location
    assert "state=" in location
    assert "hd=crewai.com" in location


def test_callback_rejects_a_mismatched_state(sso):
    """Without this an attacker could feed a victim's browser their own code."""
    client = sso.app.test_client()
    client.get("/auth/google")

    response = client.get("/auth/google/callback?code=abc&state=not-the-state")

    assert response.status_code == 400
    assert client.get("/api/channels").status_code == 401


def _complete_google_flow(sso, monkeypatch, userinfo):
    import auth as auth_module

    monkeypatch.setattr(
        auth_module, "exchange_code", lambda code, uri: {"access_token": "tok"}
    )
    monkeypatch.setattr(auth_module, "fetch_userinfo", lambda token: userinfo)

    client = sso.app.test_client()
    state = client.get("/auth/google").headers["Location"].split("state=")[1].split("&")[0]
    response = client.get(f"/auth/google/callback?code=abc&state={state}")
    return client, response


def test_allowed_domain_signs_in(sso, monkeypatch):
    client, response = _complete_google_flow(
        sso,
        monkeypatch,
        {"sub": "1", "email": "tom@crewai.com", "email_verified": True, "name": "Tom"},
    )

    assert response.status_code == 302
    assert client.get("/api/channels").status_code == 200
    assert client.get("/api/me").get_json()["email"] == "tom@crewai.com"


def test_other_domains_are_rejected(sso, monkeypatch):
    client, response = _complete_google_flow(
        sso,
        monkeypatch,
        {"sub": "2", "email": "someone@gmail.com", "email_verified": True},
    )

    assert response.status_code == 401
    assert client.get("/api/channels").status_code == 401


def test_unverified_email_is_rejected(sso, monkeypatch):
    """An unverified address can be set to anything, which would make the domain
    check meaningless."""
    client, response = _complete_google_flow(
        sso,
        monkeypatch,
        {"sub": "3", "email": "tom@crewai.com", "email_verified": False},
    )

    assert response.status_code == 401
    assert client.get("/api/channels").status_code == 401


def test_identity_is_keyed_on_google_subject_not_email(sso, monkeypatch):
    """Integration tokens will hang off this key, so it must survive a rename."""
    client, _ = _complete_google_flow(
        sso,
        monkeypatch,
        {"sub": "stable-123", "email": "tom@crewai.com", "email_verified": True},
    )

    assert client.get("/api/me").get_json()["user_id"] == "stable-123"
