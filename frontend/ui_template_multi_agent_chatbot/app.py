import hmac
import json
import logging
import os
import queue
import secrets
import sys
import threading
import time
from collections import deque
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

import auth
import db
import requests as http_requests
from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)

dotenv_path = os.path.join(os.path.dirname(__file__), "..", ".env")
load_dotenv(dotenv_path)

DEPLOYMENT_URL = os.environ.get("DEPLOYMENT_URL", "").strip().rstrip("/")
DEPLOYMENT_KEY = os.environ.get("DEPLOYMENT_KEY", "").strip()
WEBHOOK_TOKEN = os.environ.get("WEBHOOK_TOKEN", "").strip()
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

# Shared password for the whole UI. Unset means no gate: a fresh clone should run
# locally without ceremony, but anything reachable from the internet must set it —
# every message spends real API budget.
APP_PASSWORD = os.environ.get("APP_PASSWORD", "").strip()

# Signs the session cookie. Derived from APP_PASSWORD when unset so logins survive
# a dyno restart; a random per-boot key would silently log everyone out instead.
SECRET_KEY = (
    os.environ.get("SECRET_KEY", "").strip()
    or (f"derived-from-app-password:{APP_PASSWORD}" if APP_PASSWORD else "")
    or secrets.token_urlsafe(32)
)

# Per-session send limits. In-process on purpose: the deployment runs a single
# worker, so a shared store would be complexity without benefit.
RATE_LIMIT_MESSAGES = int(os.environ.get("RATE_LIMIT_MESSAGES", "30"))
RATE_LIMIT_WINDOW = int(os.environ.get("RATE_LIMIT_WINDOW", "600"))
DAILY_MESSAGE_CAP = int(os.environ.get("DAILY_MESSAGE_CAP", "500"))


def _missing_config() -> list[str]:
    """Required settings that are absent or blank.

    These can only be filled in after the flow is deployed, so a fresh clone
    always starts without them. Reported up front and again on send: an empty
    DEPLOYMENT_URL otherwise surfaces as `Invalid URL '/kickoff'`, which gives
    no hint that the real problem is unset configuration.
    """
    return [
        name
        for name, value in (
            ("DEPLOYMENT_URL", DEPLOYMENT_URL),
            ("DEPLOYMENT_KEY", DEPLOYMENT_KEY),
            ("WEBHOOK_TOKEN", WEBHOOK_TOKEN),
        )
        if not value
    ]

# Events AMP relays via webhook. llm_thinking_chunk, image_generated and the
# conversation_* events are intentionally omitted here: the flow pushes those
# straight to the channel webhook (see ConversationalEventListener) to avoid
# duplicate delivery. The conversation events are part of CrewAI's experimental
# surface, so we don't assume AMP's relay list recognizes those names at all.
WEBHOOK_EVENTS = [
    "flow_started",
    "flow_finished",
    "llm_stream_chunk",
    "tool_usage_started",
    "tool_usage_finished",
    "tool_usage_error",
]

# AMP relays flow_finished for internal sub-flows too (notably the
# "AgentExecutor" flow that wraps each agent run, whose result is just the
# status string "completed"). Only this top-level flow carries the real
# conversation result, so we finalize on it alone.
TOP_LEVEL_FLOW_NAME = "ConversationalFlow"

# Bare flow/agent status tokens that must never be rendered as an answer.
_STATUS_TOKENS = {
    "completed",
    "success",
    "succeeded",
    "finished",
    "failed",
    "error",
    "running",
    "pending",
    "cancelled",
    "canceled",
}

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = SECRET_KEY
db.init_db()

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

# Reachable without a session. The webhook is the important one: AMP calls it
# with a bearer token and has no cookie, so gating it would silently break every
# reply. It authenticates via WEBHOOK_TOKEN instead (see webhook()).
_PUBLIC_ENDPOINTS = {
    "login",
    "google_login",
    "google_callback",
    "webhook",
    "webhook_preflight",
    "static",
}


def _auth_enabled() -> bool:
    """Auth ladder: Google SSO if configured, else shared password, else open.

    The ladder exists so a fresh clone runs with no setup, a quick internal
    deploy needs only a password, and the full per-user identity is available
    without a second code path.
    """
    return auth.is_configured() or bool(APP_PASSWORD)


def _is_authenticated() -> bool:
    return not _auth_enabled() or session.get("authenticated") is True


@app.before_request
def _require_login():
    if request.endpoint in _PUBLIC_ENDPOINTS or _is_authenticated():
        return None
    # API callers get JSON they can render; browsers get the login page.
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not signed in."}), 401
    return redirect(url_for("login", next=request.path))


def _redirect_uri() -> str:
    """Callback Google returns to. Must match the Console entry exactly."""
    base = PUBLIC_BASE_URL or request.url_root.rstrip("/")
    return f"{base}/auth/google/callback"


def _safe_next(target: str | None) -> str:
    """Only ever redirect within this app — an absolute URL here would make the
    ?next= parameter an open redirect."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("index")


@app.route("/auth/google")
def google_login():
    state = auth.new_state()
    session["oauth_state"] = state
    session["oauth_next"] = _safe_next(request.args.get("next"))
    return redirect(auth.authorization_url(_redirect_uri(), state))


@app.route("/auth/google/callback")
def google_callback():
    # Compare against the state we issued: without this, an attacker could feed
    # a victim's browser their own authorization code.
    expected = session.pop("oauth_state", None)
    if not expected or request.args.get("state") != expected:
        return render_template("login.html", error="Sign-in expired. Try again."), 400

    if error := request.args.get("error"):
        return render_template("login.html", error=f"Google reported: {error}"), 400

    code = request.args.get("code")
    if not code:
        return render_template("login.html", error="No authorization code."), 400

    try:
        tokens = auth.exchange_code(code, _redirect_uri())
        identity = auth.verify_user(auth.fetch_userinfo(tokens["access_token"]))
    except auth.AuthError as exc:
        app.logger.warning("Google sign-in rejected: %s", exc)
        return render_template("login.html", error=str(exc)), 401

    session["authenticated"] = True
    session["user"] = identity
    session.permanent = True
    app.logger.info("Signed in: %s", identity["email"])

    return redirect(session.pop("oauth_next", None) or url_for("index"))


@app.route("/api/me")
def me():
    return jsonify(session.get("user") or {"email": None})


@app.route("/login", methods=["GET", "POST"])
def login():
    if not _auth_enabled() or _is_authenticated():
        return redirect(url_for("index"))

    # SSO takes precedence: with a Google client configured there is no password
    # to type, so send the browser straight to Google.
    if auth.is_configured():
        return render_template(
            "login.html",
            google_login_url=url_for("google_login", next=request.args.get("next")),
            allowed_domains=auth.ALLOWED_EMAIL_DOMAINS,
        )

    error = None
    if request.method == "POST":
        supplied = (request.form.get("password") or "").strip()
        # compare_digest: don't leak the password through response timing.
        if hmac.compare_digest(supplied, APP_PASSWORD):
            session["authenticated"] = True
            session.permanent = True
            return redirect(_safe_next(request.args.get("next")))
        error = "Incorrect password."
        app.logger.warning("Failed login attempt from %s", request.remote_addr)

    return render_template("login.html", error=error), (401 if error else 200)


@app.route("/logout", methods=["POST", "GET"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

_send_times: dict[str, deque] = {}
_daily_count = {"day": None, "count": 0}
_rate_lock = threading.Lock()


def _rate_limit_error() -> str | None:
    """Return an error message when this caller has sent too much, else None.

    Two independent limits: a per-session sliding window (one person hammering
    the box) and a process-wide daily cap (the whole demo running away). Both are
    about protecting the API budget, not security.
    """
    now = time.time()
    today = time.strftime("%Y-%m-%d", time.gmtime(now))

    if "rate_id" not in session:
        session["rate_id"] = secrets.token_urlsafe(8)
    caller = session["rate_id"]

    with _rate_lock:
        if _daily_count["day"] != today:
            _daily_count.update(day=today, count=0)
        if DAILY_MESSAGE_CAP and _daily_count["count"] >= DAILY_MESSAGE_CAP:
            return (
                f"Daily limit reached ({DAILY_MESSAGE_CAP} messages). "
                "This protects the shared API budget — try again tomorrow, or "
                "raise DAILY_MESSAGE_CAP."
            )

        history = _send_times.setdefault(caller, deque())
        while history and now - history[0] > RATE_LIMIT_WINDOW:
            history.popleft()

        if RATE_LIMIT_MESSAGES and len(history) >= RATE_LIMIT_MESSAGES:
            retry_in = int(RATE_LIMIT_WINDOW - (now - history[0])) + 1
            return (
                f"Rate limit reached ({RATE_LIMIT_MESSAGES} messages per "
                f"{RATE_LIMIT_WINDOW // 60} min). Try again in {retry_in}s."
            )

        history.append(now)
        _daily_count["count"] += 1

    return None


if not _auth_enabled():
    app.logger.warning(
        "APP_PASSWORD is not set — the UI is UNAUTHENTICATED. Fine locally; set "
        "it before exposing this anywhere, or anyone with the URL can spend your "
        "API budget."
    )

if _missing_config():
    app.logger.warning(
        "Not connected to CrewAI AMP — %s not set in frontend/.env. "
        "The UI will start and channels will work, but sending a message will "
        "fail until the flow is deployed and these are filled in.",
        ", ".join(_missing_config()),
    )


def _public_base_url() -> str:
    """Public base URL AMP should call back. Prefers the explicit
    PUBLIC_BASE_URL env var; otherwise derives it from the current request."""
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    return request.url_root.rstrip("/")


@app.after_request
def _add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    return response


_sse_subscribers: dict[str, list[queue.Queue]] = {}
_sse_lock = threading.Lock()

_active_thinking: dict[str, dict] = {}
_active_responses: dict[str, dict] = {}

_last_event_at: dict[str, float] = {}
_pending_kickoffs: dict[str, str] = {}
_finalized_kickoffs: set[str] = set()
_kickoff_lock = threading.Lock()

_IDLE_TIMEOUT = 10
# How long a single turn may take before the UI stops waiting. Research and
# Slack turns routinely run past a minute — several tool calls, each with its own
# latency — so a 60s ceiling abandoned answers that were still being produced.
_WATCHDOG_MAX_LIFETIME = int(os.environ.get("TURN_TIMEOUT", "300"))


def _parse_dt(val) -> float:
    """Parse an ISO-8601 datetime string to an epoch timestamp."""
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return datetime.fromisoformat(str(val)).timestamp()
    except (ValueError, TypeError):
        return time.time()


def _broadcast_to_channel(channel_id: str, event_data: dict):
    with _sse_lock:
        subscribers = _sse_subscribers.get(channel_id, [])
        for q in subscribers:
            q.put(event_data)


def _crewai_headers():
    return {
        "Authorization": f"Bearer {DEPLOYMENT_KEY}",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# AMP wakeup (debounced to once per 10 minutes)
# ---------------------------------------------------------------------------

_WAKEUP_INTERVAL = 600
_last_wakeup: float = 0
_wakeup_lock = threading.Lock()


@app.route("/api/wakeup", methods=["POST"])
def wakeup():
    global _last_wakeup

    with _wakeup_lock:
        if time.monotonic() - _last_wakeup < _WAKEUP_INTERVAL:
            return jsonify({"status": "already_awake"}), 200
        _last_wakeup = time.monotonic()

    def _ping():
        try:
            resp = http_requests.get(
                f"{DEPLOYMENT_URL}/inputs",
                headers=_crewai_headers(),
                timeout=30,
            )
            resp.raise_for_status()
            app.logger.info("AMP wakeup OK (%s)", resp.status_code)
        except Exception as e:
            app.logger.warning("AMP wakeup failed: %s", e)

    threading.Thread(target=_ping, daemon=True).start()
    return jsonify({"status": "waking"}), 202


# ---------------------------------------------------------------------------
# Channel API
# ---------------------------------------------------------------------------


@app.route("/api/channels", methods=["GET"])
def list_channels():
    return jsonify(db.get_channels())


@app.route("/api/channels", methods=["POST"])
def create_channel():
    data = request.get_json(force=True)
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    channel = db.create_channel(name)
    return jsonify(channel), 201


@app.route("/api/channels/<channel_id>", methods=["GET"])
def get_channel(channel_id):
    channel = db.get_channel(channel_id)
    if not channel:
        return jsonify({"error": "not found"}), 404
    return jsonify(channel)


@app.route("/api/channels/<channel_id>", methods=["DELETE"])
def delete_channel(channel_id):
    db.delete_channel(channel_id)
    return "", 204


# ---------------------------------------------------------------------------
# Messages / Kickoff
# ---------------------------------------------------------------------------


@app.route("/api/channels/<channel_id>/messages", methods=["POST"])
def send_message(channel_id):
    channel = db.get_channel(channel_id)
    if not channel:
        return jsonify({"error": "channel not found"}), 404

    data = request.get_json(force=True)
    content = data.get("content", "").strip()
    if not content:
        return jsonify({"error": "content is required"}), 400

    rate_error = _rate_limit_error()
    if rate_error:
        return jsonify({"error": rate_error}), 429

    missing = _missing_config()
    if missing:
        return jsonify(
            {
                "error": (
                    f"Not connected to CrewAI AMP — {', '.join(missing)} "
                    "not set in frontend/.env. Deploy the flow first "
                    "(crewai deploy push), then copy the deployment URL and "
                    "bearer token from the AMP dashboard."
                )
            }
        ), 503

    msg = db.add_message(channel_id, role="user", content=content)

    callback_url = f"{_public_base_url()}/api/webhook/{channel_id}"

    # One kickoff == one chat turn. `id` is the conversation session: the flow is
    # @persist()ed, so passing the same id restores the transcript on whatever
    # fresh AMP process handles this turn. It must stay stable for the life of the
    # channel — this is why it's the channel's conversation_id and not the
    # previous kickoff_id we used to pass as restoreFromStateId.
    kickoff_body = {
        "inputs": {
            "id": channel["conversation_id"],
            "user_message": content,
            "webhook_url": callback_url,
        },
        "webhooks": {
            "events": WEBHOOK_EVENTS,
            "url": callback_url,
            "realtime": True,
            "authentication": {"strategy": "bearer", "token": WEBHOOK_TOKEN},
        },
    }

    app.logger.info(
        "Kickoff -> channel=%s session=%s content=%s",
        channel_id,
        channel["conversation_id"],
        content[:80],
    )

    def _do_kickoff():
        try:
            resp = http_requests.post(
                f"{DEPLOYMENT_URL}/kickoff",
                headers=_crewai_headers(),
                json=kickoff_body,
                timeout=30,
            )
            if not resp.ok:
                app.logger.error(
                    "Kickoff HTTP %s: %s", resp.status_code, resp.text[:500]
                )
            resp.raise_for_status()
            result = resp.json()
            kickoff_id = result.get("kickoff_id")
            app.logger.info("Kickoff OK: %s", kickoff_id)
            if kickoff_id:
                # Tracked in memory only, for the watchdog and finalize dedupe.
                # Session continuity now rides on inputs["id"], not this id.
                with _kickoff_lock:
                    _pending_kickoffs[channel_id] = kickoff_id
                    _finalized_kickoffs.discard(kickoff_id)
                    _last_event_at[channel_id] = time.time()
                threading.Thread(
                    target=_status_watchdog,
                    args=(channel_id, kickoff_id),
                    daemon=True,
                ).start()
            _broadcast_to_channel(
                channel_id,
                {"type": "kickoff_started", "kickoff_id": kickoff_id},
            )
        except Exception as e:
            app.logger.error("Kickoff failed: %s", e)
            _broadcast_to_channel(
                channel_id,
                {"type": "kickoff_error", "error": str(e)},
            )

    threading.Thread(target=_do_kickoff, daemon=True).start()

    return jsonify({"status": "sent", "message": msg}), 202


# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------


@app.route("/api/channels/<channel_id>/events", methods=["GET"])
def channel_events(channel_id):
    def stream():
        q = queue.Queue()
        with _sse_lock:
            _sse_subscribers.setdefault(channel_id, []).append(q)
        try:
            while True:
                try:
                    event = q.get(timeout=25)
                    yield f"data: {json.dumps(event, default=str)}\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            pass
        finally:
            with _sse_lock:
                subs = _sse_subscribers.get(channel_id, [])
                if q in subs:
                    subs.remove(q)

    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# Webhook receiver (realtime events from AMP, scoped per channel via URL)
# ---------------------------------------------------------------------------


@app.route("/api/webhook/<channel_id>", methods=["OPTIONS"])
def webhook_preflight(channel_id):
    return "", 204


@app.route("/api/webhook/<channel_id>", methods=["POST"])
def webhook(channel_id):
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {WEBHOOK_TOKEN}":
        return jsonify({"error": "unauthorized"}), 401

    channel = db.get_channel(channel_id)
    if not channel:
        return jsonify({"status": "ignored", "reason": "unknown channel"}), 200

    try:
        payload = request.get_json(force=True)
    except Exception:
        return "bad json", 400

    events = payload.get("events") if isinstance(payload, dict) else None
    if events is None:
        events = [payload]

    for ev in events:
        etype = ev.get("type") if isinstance(ev, dict) else None
        app.logger.info("Webhook received event channel=%s type=%s", channel_id, etype)
        try:
            _handle_event(channel_id, ev)
        except Exception as e:
            app.logger.warning("Failed to handle event (type=%s): %s", etype, e)

    return jsonify({"status": "ok"}), 200


def _handle_event(channel_id: str, ev: dict):
    etype = ev.get("type")
    execution_id = ev.get("execution_id")
    d = ev.get("data") or {}
    seq = d.get("emission_sequence", 0)
    agent_role = d.get("agent_role", "")

    _last_event_at[channel_id] = time.time()

    app.logger.info(
        "Webhook event channel=%s type=%s seq=%s call_id=%s",
        channel_id,
        etype,
        seq,
        d.get("call_id"),
    )

    if etype == "flow_started":
        _active_thinking.pop(channel_id, None)
        _active_responses.pop(channel_id, None)
        _broadcast_to_channel(channel_id, {"type": "flow_started", "seq": seq})

    elif etype == "llm_stream_chunk":
        chunk_text = d.get("chunk", "")
        if d.get("tool_call"):
            return
        if not chunk_text:
            return

        call_id = d.get("call_id")
        ar = _active_responses.get(channel_id)
        if ar is None or ar.get("call_id") != call_id:
            is_structured = chunk_text.lstrip().startswith(("{", "["))
            ar = {"call_id": call_id, "text": "", "structured": is_structured}
            _active_responses[channel_id] = ar
        if not ar.get("structured"):
            ar["text"] += chunk_text

        _broadcast_to_channel(
            channel_id,
            {
                "type": "llm_stream_chunk",
                "call_id": call_id,
                "chunk": chunk_text,
                "agent_role": agent_role,
                "seq": seq,
            },
        )

    elif etype == "llm_thinking_chunk":
        call_id = d.get("call_id")
        chunk_text = d.get("chunk", "")

        at = _active_thinking.get(channel_id)
        if not at or at["call_id"] != call_id:
            at = {"call_id": call_id, "text": ""}
            _active_thinking[channel_id] = at
        at["text"] += chunk_text

        db.upsert_thinking(
            channel_id,
            event_id=f"thinking:{call_id}",
            content=at["text"],
            agent_role=agent_role,
        )

        _broadcast_to_channel(
            channel_id,
            {
                "type": "llm_thinking_chunk",
                "call_id": call_id,
                "chunk": chunk_text,
                "agent_role": agent_role,
                "seq": seq,
            },
        )

    elif etype == "tool_usage_started":
        tool_name = d.get("tool_name", "")
        if tool_name != "structured_output":
            _broadcast_to_channel(
                channel_id,
                {
                    "type": "tool_usage_started",
                    "tool_name": tool_name,
                    "agent_role": agent_role,
                    "seq": seq,
                },
            )

    elif etype == "tool_usage_finished":
        tool_name = d.get("tool_name", "")
        if not tool_name or tool_name == "structured_output":
            return
        start_ts = _parse_dt(d.get("started_at"))
        end_ts = _parse_dt(d.get("finished_at"))
        duration_s = round(end_ts - start_ts, 1)
        db.add_message(
            channel_id,
            role="assistant",
            content=tool_name,
            event_type="tool_usage",
            event_id=d.get("event_id") or ev.get("id"),
            agent_role=agent_role,
            timeline=json.dumps({"duration_s": duration_s}),
        )
        _broadcast_to_channel(
            channel_id,
            {
                "type": "tool_usage_finished",
                "tool_name": tool_name,
                "duration_s": duration_s,
                "agent_role": agent_role,
                "seq": seq,
            },
        )
        app.logger.info(
            "Tool finished: %s (%.1fs) channel=%s",
            tool_name,
            duration_s,
            channel_id[:12],
        )

    elif etype == "tool_usage_error":
        tool_name = d.get("tool_name", "")
        if tool_name != "structured_output":
            error_msg = str(d.get("error", "Unknown error"))
            _broadcast_to_channel(
                channel_id,
                {
                    "type": "tool_usage_error",
                    "tool_name": tool_name,
                    "error": error_msg,
                    "seq": seq,
                },
            )
            app.logger.warning(
                "Tool error: %s — %s channel=%s",
                tool_name,
                error_msg[:100],
                channel_id[:12],
            )

    elif etype == "image_generated":
        result = d.get("result", {})
        image_b64 = result.get("image", "")

        db.add_message(
            channel_id,
            role="assistant",
            content="",
            event_type="image_generated",
            image_base64=image_b64,
            event_id=d.get("event_id") or ev.get("id"),
            agent_role=agent_role,
        )

        _broadcast_to_channel(
            channel_id,
            {
                "type": "image_generated",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "event_type": "image_generated",
                    "image_base64": image_b64,
                    "event_id": d.get("event_id") or ev.get("id"),
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
                },
                "seq": seq,
            },
        )

    elif etype == "conversation_route_selected":
        # Which handler the router picked for this turn. Purely presentational,
        # but it's the clearest way to show multi-agent routing during a demo.
        _broadcast_to_channel(
            channel_id,
            {
                "type": "conversation_route_selected",
                "route": d.get("route", ""),
                "previous_route": d.get("previous_intent"),
                "seq": seq,
            },
        )

    elif etype == "conversation_turn_completed":
        # Safety net: fires after the flow returns, so it still lands if AMP
        # dropped flow_finished. Carries no result, so the text comes from the
        # streamed accumulation. _finalize_response dedupes per kickoff, so
        # whichever of the two arrives first wins.
        ar = _active_responses.get(channel_id)
        final_text = (ar.get("text") or "").strip() if ar else ""
        _finalize_response(
            channel_id,
            _pending_kickoffs.get(channel_id) or execution_id or "",
            final_text,
            call_id=ar.get("call_id") if ar else None,
            seq=seq,
            agent_role=agent_role,
        )

    elif etype == "flow_finished":
        # Ignore flow_finished from internal sub-flows (e.g. "AgentExecutor"):
        # their result is the status string "completed", and finalizing on them
        # both leaks "completed" into the chat and dedupes out the real answer.
        if d.get("flow_name") != TOP_LEVEL_FLOW_NAME:
            return
        ar = _active_responses.get(channel_id)
        final_text = _result_to_text(d.get("result")) or _result_to_text(d.get("state"))
        if not final_text and ar:
            final_text = (ar.get("text") or "").strip()
        kickoff_id = _pending_kickoffs.get(channel_id) or execution_id or ""
        _finalize_response(
            channel_id,
            kickoff_id,
            final_text,
            call_id=ar.get("call_id") if ar else None,
            seq=seq,
            agent_role=agent_role,
        )


# ---------------------------------------------------------------------------
# Flow finalization helpers
# ---------------------------------------------------------------------------


def _extract_assistant_text(messages) -> str:
    for m in reversed(messages or []):
        if m.get("role") == "assistant" and (m.get("content") or "").strip():
            return m["content"]
    return ""


def _result_to_text(result) -> str:
    """Best-effort extraction of the final assistant answer from a flow result."""
    if result is None:
        return ""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (ValueError, TypeError):
            stripped = result.strip()
            return "" if stripped.lower() in _STATUS_TOKENS else stripped
    if isinstance(result, dict):
        text = _extract_assistant_text(result.get("messages"))
        if text:
            return text
        for key in ("raw", "output", "answer", "content", "result"):
            val = result.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


def _finalize_response(
    channel_id, kickoff_id, final_text, call_id=None, seq=0, agent_role=""
):
    """Persist the final assistant message and tell the UI to render the
    authoritative text. Deduped per kickoff so the realtime flow_finished and
    the status watchdog can't both fire."""
    with _kickoff_lock:
        if kickoff_id and kickoff_id in _finalized_kickoffs:
            return
        if kickoff_id:
            _finalized_kickoffs.add(kickoff_id)
        if _pending_kickoffs.get(channel_id) == kickoff_id:
            _pending_kickoffs.pop(channel_id, None)

    _active_thinking.pop(channel_id, None)
    _active_responses.pop(channel_id, None)

    if final_text:
        try:
            db.add_message(
                channel_id,
                role="assistant",
                content=final_text,
                event_type="assistant_message",
                event_id=f"result:{kickoff_id}",
                agent_role=agent_role,
            )
        except Exception as e:
            app.logger.warning("Failed to persist final message: %s", e)

    _broadcast_to_channel(
        channel_id,
        {
            "type": "flow_finished",
            "seq": seq,
            "text": final_text,
            "call_id": call_id,
        },
    )


# ---------------------------------------------------------------------------
# Status watchdog (fallback for dropped webhooks)
# ---------------------------------------------------------------------------


def _fetch_status(kickoff_id):
    try:
        resp = http_requests.get(
            f"{DEPLOYMENT_URL}/status/{kickoff_id}",
            headers=_crewai_headers(),
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        app.logger.warning("Status poll failed for %s: %s", kickoff_id, e)
        return None


def _status_watchdog(channel_id, kickoff_id):
    """Fallback for dropped realtime webhooks: if no events arrive for
    _IDLE_TIMEOUT seconds and the kickoff hasn't finished, poll AMP's status
    endpoint and finalize from the authoritative result once it completes."""
    started = time.monotonic()
    while time.monotonic() - started < _WATCHDOG_MAX_LIFETIME:
        time.sleep(2)

        with _kickoff_lock:
            still_pending = _pending_kickoffs.get(channel_id) == kickoff_id
        if not still_pending:
            return

        idle = time.time() - _last_event_at.get(channel_id, started)
        if idle < _IDLE_TIMEOUT:
            continue

        status = _fetch_status(kickoff_id)
        if not status:
            continue

        state = str(status.get("state") or status.get("status") or "").upper()
        if state in ("SUCCESS", "COMPLETED", "FINISHED", "SUCCEEDED"):
            final_text = _result_to_text(status.get("result"))
            ar = _active_responses.get(channel_id)
            app.logger.info(
                "Watchdog finalizing channel=%s kickoff=%s after %.1fs idle",
                channel_id,
                kickoff_id,
                idle,
            )
            _finalize_response(
                channel_id,
                kickoff_id,
                final_text,
                call_id=ar.get("call_id") if ar else None,
            )
            return
        if state in ("FAILED", "ERROR", "CANCELLED", "CANCELED", "NOT_FOUND"):
            app.logger.warning(
                "Watchdog: execution %s ended in state=%s", kickoff_id, state
            )
            _finalize_response(channel_id, kickoff_id, "")
            return

    app.logger.warning(
        "Watchdog gave up on kickoff=%s after %ss", kickoff_id, _WATCHDOG_MAX_LIFETIME
    )
    with _kickoff_lock:
        _pending_kickoffs.pop(channel_id, None)

    # Tell the browser. Dropping out silently leaves the typing indicator
    # spinning forever, which reads as a hung app rather than a slow turn.
    _broadcast_to_channel(
        channel_id,
        {
            "type": "kickoff_error",
            "error": (
                f"No response after {_WATCHDOG_MAX_LIFETIME}s. The run may still "
                "be going on AMP — check its logs, or raise TURN_TIMEOUT."
            ),
        },
    )


# ---------------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5005)),
        debug=True,
        threaded=True,
    )
