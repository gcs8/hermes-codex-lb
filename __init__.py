"""codex-lb plugin: agent half.

* ``llm_request`` middleware adds ``service_tier: "priority"`` to requests bound for a
  configured codex-lb base URL when the session's fast mode says so.
* ``llm_execution`` middleware records the tier the response was actually served at.
* ``/lbfast on|off|auto|status`` sets the mode for the current session.

Verified on Hermes 0.22.0 in the CLI, tui_gateway, and the turn-isolation compute-host child
(docs/probes/).
"""

from __future__ import annotations

import logging
from typing import Any

from . import lbcore

log = logging.getLogger(__name__)

_SOURCE = "codex-lb"


def _on_llm_request(
    request: Any = None, base_url: Any = None, session_id: Any = None, api_call_count: Any = None, **_: Any
) -> Any:
    if not isinstance(request, dict) or not lbcore.is_codex_lb_url(base_url):
        return None
    if request.get("service_tier"):
        return None  # something upstream already chose a tier; leave it alone
    if not lbcore.wants_priority(str(session_id or ""), api_call_count):
        return None
    return {
        "request": {**request, "service_tier": lbcore.PRIORITY},
        "source": _SOURCE,
        "reason": "fast mode on for this session",
    }


def _on_llm_execution(
    request: Any = None, next_call: Any = None, base_url: Any = None, session_id: Any = None, **_: Any
) -> Any:
    result = next_call(request)
    if lbcore.is_codex_lb_url(base_url):
        try:
            requested = (request or {}).get("service_tier") if isinstance(request, dict) else None
            lbcore.record_tier(str(session_id or ""), requested, getattr(result, "service_tier", None))
        except Exception:  # counters must never fail a model call
            log.debug("codex-lb: could not record served tier", exc_info=True)
    return result


def _session_id() -> str:
    """The session a slash command was typed in.

    Hermes has no public plugin API for this yet, so this reads the same session context the
    command dispatcher binds (``HERMES_SESSION_ID``). That matches the ``session_id`` the
    middleware receives, including across the turn-isolation process split (docs/probes/).
    """
    try:
        from gateway.session_context import get_session_env
    except ImportError:
        import os

        return os.environ.get("HERMES_SESSION_ID", "")
    return get_session_env("HERMES_SESSION_ID") or ""


def _status_text(session_id: str) -> str:
    current = lbcore.get_fast(session_id)
    summary = lbcore.tier_summary(session_id)
    lines = [f"codex-lb fast mode: {current}"]
    if summary["priority_requested"]:
        lines.append(
            f"priority requested {summary['priority_requested']}x, "
            f"served {summary['priority_served']}x, downgraded {summary['downgraded']}x"
        )
        if summary["downgraded"] and not summary["priority_served"]:
            lines.append("codex-lb served every priority request at the default tier.")
    if (last := summary["last"]) is not None:
        lines.append(f"last request: asked {last['requested']}, served {last['served']}")
    if not lbcore.base_urls():
        lines.append("No codex-lb base URL configured: set base_urls in the plugin's settings.")
    return "\n".join(lines)


def _lbfast(raw_args: str) -> str:
    session_id = _session_id()
    arg = (raw_args or "").strip().lower()
    if arg in ("", "status"):
        return _status_text(session_id)
    if arg not in lbcore.FAST_MODES:
        return "Usage: /lbfast on|off|auto|status"
    if not session_id:
        return "No session to apply this to."
    lbcore.set_fast(session_id, arg)
    note = " The priority tier uses more of your subscription quota." if arg != "off" else ""
    return f"codex-lb fast mode {arg} for this session.{note}"


def register(ctx) -> None:
    ctx.register_middleware("llm_request", _on_llm_request)
    ctx.register_middleware("llm_execution", _on_llm_execution)
    ctx.register_command(
        "lbfast", _lbfast, description="codex-lb fast tier for this session", args_hint="on|off|auto|status"
    )
