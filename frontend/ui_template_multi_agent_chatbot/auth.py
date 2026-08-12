"""Google OAuth helpers.

The authorization-code flow, kept out of app.py so the route handlers stay thin.

Identity is the point, not just the gate: per-user integrations need a stable
per-user key to hang tokens on, and a shared password can't provide one. The
same OAuth client can later request Gmail/Calendar/Drive scopes incrementally,
so those integrations come without a second OAuth app.
"""

import os
import secrets
from urllib.parse import urlencode

import requests

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()

# Only these domains may sign in. Comma-separated; blank means any Google account,
# which is almost never what you want for an internal demo.
ALLOWED_EMAIL_DOMAINS = tuple(
    domain.strip().lower()
    for domain in os.environ.get("ALLOWED_EMAIL_DOMAINS", "crewai.com").split(",")
    if domain.strip()
)

_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v3/userinfo"

BASE_SCOPES = ("openid", "email", "profile")


class AuthError(Exception):
    """Sign-in failed for a reason worth showing the user."""


def is_configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def new_state() -> str:
    """CSRF token tying the callback back to the browser that started the flow."""
    return secrets.token_urlsafe(24)


def authorization_url(redirect_uri: str, state: str, scopes=BASE_SCOPES) -> str:
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "state": state,
        # Nudges the account chooser toward the right domain. It is a hint only —
        # Google does not enforce it, so the claim is re-checked after callback.
        "hd": ALLOWED_EMAIL_DOMAINS[0] if ALLOWED_EMAIL_DOMAINS else None,
        "prompt": "select_account",
        # Needed only once we start requesting Gmail/Calendar scopes, but asking
        # now means the refresh token exists before it's required.
        "access_type": "offline",
        "include_granted_scopes": "true",
    }
    return f"{_AUTH_ENDPOINT}?{urlencode({k: v for k, v in params.items() if v})}"


def exchange_code(code: str, redirect_uri: str) -> dict:
    response = requests.post(
        _TOKEN_ENDPOINT,
        data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    if not response.ok:
        raise AuthError("Google rejected the sign-in. Please try again.")
    return response.json()


def fetch_userinfo(access_token: str) -> dict:
    """Read the profile straight from Google over TLS.

    Deliberately not decoding the id_token locally: doing that safely means
    fetching and caching Google's JWKS and verifying signatures. Asking Google
    directly is simpler and has the same trust properties here.
    """
    response = requests.get(
        _USERINFO_ENDPOINT,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    if not response.ok:
        raise AuthError("Could not read your Google profile. Please try again.")
    return response.json()


def verify_user(userinfo: dict) -> dict:
    """Return the session identity, or raise if this account may not sign in."""
    email = (userinfo.get("email") or "").lower()
    if not email:
        raise AuthError("Google did not return an email address.")

    # An unverified address can be set to anything, so the domain check below
    # would be worthless without this.
    if not userinfo.get("email_verified"):
        raise AuthError("Your Google email address is not verified.")

    if ALLOWED_EMAIL_DOMAINS:
        domain = email.rpartition("@")[2]
        if domain not in ALLOWED_EMAIL_DOMAINS:
            raise AuthError(
                f"{email} is not allowed. Sign in with a "
                f"{' or '.join('@' + d for d in ALLOWED_EMAIL_DOMAINS)} account."
            )

    return {
        # Google's subject id, not the email: stable even if the address changes,
        # which matters once integration tokens are keyed on it.
        "user_id": userinfo.get("sub") or email,
        "email": email,
        "name": userinfo.get("name") or email,
        "picture": userinfo.get("picture"),
    }


# Scopes requested when a user connects mail/calendar. Kept separate from
# BASE_SCOPES so signing in stays a low-friction consent and the heavier grant is
# asked for only when someone actually wants it.
GOOGLE_DATA_SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
)


def refresh_access_token(refresh_token: str) -> dict:
    """Exchange a refresh token for a fresh access token.

    Google access tokens last an hour, so anything long-running has to refresh.
    The response does NOT include a new refresh token — the stored one stays
    valid and must be kept.
    """
    response = requests.post(
        _TOKEN_ENDPOINT,
        data={
            "refresh_token": refresh_token,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "grant_type": "refresh_token",
        },
        timeout=15,
    )
    if not response.ok:
        raise AuthError("Could not refresh Google access. Reconnect your account.")
    return response.json()
