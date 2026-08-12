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

    # `auth` captures GOOGLE_CLIENT_ID at import, so it must be reloaded under
    # this scenario's environment too. Reloading only `app` let a previous SSO
    # test leave Google credentials in place, silently switching later
    # password-path tests to the Google login page.
    import auth as auth_module

    importlib.reload(auth_module)

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
    return _load_app(monkeypatch, **GOOGLE_ENV)


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


# ---------------------------------------------------------------------------
# Per-user credentials
# ---------------------------------------------------------------------------


def test_credentials_endpoint_requires_the_shared_secret(client):
    """The caller is a server, not a browser, so it's exempt from the session
    gate — which makes the token check the only thing protecting it."""
    assert client.get("/api/internal/credentials/u1").status_code == 401
    assert client.get(
        "/api/internal/credentials/u1", headers={"Authorization": "Bearer wrong"}
    ).status_code == 401


def test_credentials_endpoint_returns_only_that_users_grants(client):
    import db

    db.save_credentials("user-a", "slack", "token-a")
    db.save_credentials("user-b", "slack", "token-b")
    headers = {"Authorization": f"Bearer {BASE_ENV['WEBHOOK_TOKEN']}"}

    a = client.get("/api/internal/credentials/user-a", headers=headers).get_json()
    b = client.get("/api/internal/credentials/user-b", headers=headers).get_json()
    db.delete_credentials("user-a")
    db.delete_credentials("user-b")

    assert a["providers"]["slack"]["access_token"] == "token-a"
    assert b["providers"]["slack"]["access_token"] == "token-b"


def test_kickoff_sends_user_id_but_never_tokens(client, monkeypatch):
    """Tokens in inputs would be merged into flow state, persisted by @persist,
    and deep-copied into every trace event."""
    import app as app_module

    captured = {}

    class FakeResponse:
        ok = True
        status_code = 200

        def json(self):
            return {"kickoff_id": "k1"}

        def raise_for_status(self):
            pass

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(json or {})
        return FakeResponse()

    monkeypatch.setattr(app_module.http_requests, "post", fake_post)

    _sign_in(client)
    with client.session_transaction() as sess:
        sess["user"] = {"user_id": "google-sub-1", "email": "t@crewai.com"}
    channel_id = client.post("/api/channels", json={"name": "u"}).get_json()["id"]
    _send(client, channel_id)
    for _ in range(50):
        if captured:
            break
        import time

        time.sleep(0.05)
    client.delete(f"/api/channels/{channel_id}")

    inputs = captured.get("inputs", {})
    assert inputs.get("user_id") == "google-sub-1"
    assert "credentials_url" in inputs
    assert not any("token" in k for k in inputs), inputs


# ---------------------------------------------------------------------------
# Sign out
# ---------------------------------------------------------------------------


def test_me_reports_authentication_in_password_mode(client):
    """Password mode has no email, which is otherwise indistinguishable from
    being signed out — the UI needs this to offer a sign-out control."""
    _sign_in(client)

    me = client.get("/api/me").get_json()

    assert me["authenticated"] is True
    assert me["auth_mode"] == "password"


def test_signing_out_clears_the_session(client):
    _sign_in(client)
    assert client.get("/api/channels").status_code == 200

    client.get("/logout")

    assert client.get("/api/channels").status_code == 401
    assert client.get("/api/me").status_code == 401


def test_signing_out_does_not_revoke_stored_credentials(client):
    """Signing out ends the browser session; it must not silently drop a grant
    the user deliberately made, or reconnecting would be required every time."""
    import db

    db.save_credentials("signout-user", "google", "token")
    _sign_in(client)
    client.get("/logout")
    remaining = db.get_credentials("signout-user")
    db.delete_credentials("signout-user")

    assert "google" in remaining
