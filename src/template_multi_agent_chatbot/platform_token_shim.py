"""Let CrewAI Platform tools use the signed-in user's integration token.

`crewai_tools` resolves the platform token like this:

    def get_platform_integration_token() -> str:
        return os.getenv("CREWAI_PLATFORM_INTEGRATION_TOKEN") or ""
        # TODO: Use context manager to get token

Env-only, so every user shares one org-wide identity — `apps=["slack/..."]`
cannot act as a person. Core already ships the missing half: `crewai.context`
has a `_platform_integration_token` ContextVar with a setter and a
`platform_context()` manager. The two just aren't connected, and that TODO is
presumably the plan.

This bridges them: when the current turn carries a user's platform token, that
token is used; otherwise the env var is, unchanged. Applying it means a user who
has connected Slack in their *own* CrewAI account gets answers scoped to them,
with no change to the agents.

The patch targets the two *consuming* modules rather than `misc`, because both
do `from ...misc import get_platform_integration_token` at import time and so
hold their own reference — patching `misc` alone has no effect.

Remove this once crewai_tools resolves the TODO upstream.
"""

import logging

logger = logging.getLogger(__name__)

_applied = False

# Modules that bound the symbol at import and must each be patched.
_TARGETS = (
    "crewai_tools.tools.crewai_platform_tools.crewai_platform_tool_builder",
    "crewai_tools.tools.crewai_platform_tools.crewai_platform_action_tool",
)


def apply() -> bool:
    """Install the shim. Idempotent; returns whether it is in effect."""
    global _applied
    if _applied:
        return True

    import importlib
    import os

    from template_multi_agent_chatbot import user_context

    def resolve() -> str:
        token = user_context.access_token_for("crewai_platform")
        if token:
            return token
        # Preserve the original contract, including raising when nothing is
        # configured — callers rely on that to report a setup problem.
        env_token = os.getenv("CREWAI_PLATFORM_INTEGRATION_TOKEN") or ""
        if not env_token:
            raise ValueError(
                "No platform integration token found, please set the "
                "CREWAI_PLATFORM_INTEGRATION_TOKEN environment variable"
            )
        return env_token

    patched = 0
    for module_path in _TARGETS:
        try:
            module = importlib.import_module(module_path)
        except Exception as exc:  # crewai_tools layout changed upstream
            logger.warning("Platform token shim: cannot import %s (%s)", module_path, exc)
            continue
        if hasattr(module, "get_platform_integration_token"):
            module.get_platform_integration_token = resolve
            patched += 1
        else:
            logger.warning(
                "Platform token shim: %s no longer imports the token getter — "
                "per-user platform credentials will silently fall back to the "
                "org token.",
                module_path,
            )

    _applied = patched == len(_TARGETS)
    if not _applied:
        logger.warning(
            "Platform token shim only partially applied (%d/%d).",
            patched,
            len(_TARGETS),
        )
    return _applied
