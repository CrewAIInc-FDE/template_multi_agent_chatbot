"""Shared test setup.

Everything here runs at import time, before pytest collects the test modules.
That ordering matters: both packages read configuration and build clients while
being imported, so a fixture would run too late to influence them.
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_SRC = REPO_ROOT / "src"
FRONTEND_DIR = REPO_ROOT / "frontend" / "ui_template_multi_agent_chatbot"

sys.path.insert(0, str(BACKEND_SRC))
sys.path.insert(0, str(FRONTEND_DIR))

# Credentials that must exist before import. `google.genai.Client()` is built at
# class-definition time in the image tools and refuses to construct without a
# key; dummies suffice because no test makes a real call.
_REQUIRED = {
    "GEMINI_API_KEY": "test-key",
    "SERPER_API_KEY": "test-key",
    # Enables the SLACK route. The catalog is built when routing/router_config is
    # imported, so a route's credentials must be present before that — setting
    # them inside a test is too late.
    "CREWAI_PLATFORM_INTEGRATION_TOKEN": "test-token",
    "ARIZE_API_KEY": "test-key",
    "ARIZE_PROJECT_NAME": "test",
    "ARIZE_SPACE_ID": "test",
    # Keep the suite offline: importing main.py otherwise registers a tracer and
    # starts exporting spans, turning every run into connection retries.
    "CREWAI_TRACING_ENABLED": "false",
    "CREWAI_TELEMETRY_OPT_OUT": "true",
    "OTEL_SDK_DISABLED": "true",
}

# Routes gate on credentials, so real ones must not leak in from a developer's
# shell — the MongoDB trio decides whether CREWAI_DOCS is enabled.
_MUST_BE_ABSENT = (
    "MONGODB_CONNECTION_STRING",
    "MONGODB_DATABASE_NAME",
    "MONGODB_COLLECTION_NAME",
)


def _prepare_environment() -> None:
    for key, value in _REQUIRED.items():
        os.environ[key] = value
    for key in _MUST_BE_ABSENT:
        os.environ.pop(key, None)


def _disable_dotenv() -> None:
    """Stop .env files reaching the tests.

    Both CrewAI and the Flask app call `load_dotenv()`, which would repopulate
    any variable a test just cleared — so "what happens when DEPLOYMENT_URL is
    unset" would pass locally for the wrong reason and fail in CI.
    """
    import dotenv

    dotenv.load_dotenv = lambda *args, **kwargs: False
    dotenv.main.load_dotenv = lambda *args, **kwargs: False


def _disable_tracing_exporters() -> None:
    import arize.otel
    from openinference.instrumentation.crewai import CrewAIInstrumentor

    arize.otel.register = lambda *args, **kwargs: None
    CrewAIInstrumentor.instrument = lambda self, *args, **kwargs: None


_prepare_environment()
_disable_dotenv()
_disable_tracing_exporters()
