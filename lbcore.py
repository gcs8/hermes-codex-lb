"""Shared state and codex-lb client for the codex-lb plugin.

Loaded by both halves: the agent half (``__init__.py``: middleware and ``/lbfast``) and the
dashboard backend (``dashboard/plugin_api.py``). Under ``dashboard.turn_isolation`` slash
commands run in the parent gateway process while model requests run in the compute-host
child, so nothing here may live only in memory: per-session state goes through
``plugin_db`` (SQLite, WAL) under the active profile's ``plugin-data/codex-lb/``.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any

log = logging.getLogger(__name__)

PLUGIN_ID = "codex-lb"
SECRET_NAME = "CODEX_LB_PLUGIN_API_KEY"
FAST_MODES = ("off", "on", "auto")
AUTO_WINDOW_SECONDS = 60
PRIORITY = "priority"
UNKNOWN_TIER = "unknown"
_HTTP_TIMEOUT = 10.0
_MAX_BODY = 1 << 20


# ── config ──────────────────────────────────────────────────────────────────────


def settings() -> dict[str, Any]:
    """``plugins.entries.codex-lb.settings`` from the active profile (read-only view)."""
    from hermes_cli.config import load_config_readonly

    entries = ((load_config_readonly() or {}).get("plugins") or {}).get("entries") or {}
    entry = entries.get(PLUGIN_ID) if isinstance(entries, Mapping) else None
    value = entry.get("settings") if isinstance(entry, Mapping) else None
    return dict(value) if isinstance(value, Mapping) else {}


def _norm_url(url: Any) -> str:
    return str(url or "").strip().rstrip("/").lower()


def base_urls(cfg: Mapping[str, Any] | None = None) -> list[str]:
    raw = (cfg if cfg is not None else settings()).get("base_urls") or []
    if isinstance(raw, str):
        raw = [raw]
    return [u for u in (_norm_url(x) for x in raw) if u]


def is_codex_lb_url(url: Any, cfg: Mapping[str, Any] | None = None) -> bool:
    target = _norm_url(url)
    return bool(target) and target in base_urls(cfg)


def default_fast(cfg: Mapping[str, Any] | None = None) -> str:
    value = str((cfg if cfg is not None else settings()).get("default_fast") or "off").strip().lower()
    return value if value in FAST_MODES else "off"


def mode(cfg: Mapping[str, Any] | None = None) -> str:
    value = str((cfg if cfg is not None else settings()).get("mode") or "read_only").strip().lower()
    return "read_write" if value == "read_write" else "read_only"


def api_key() -> str:
    from agent.secret_scope import get_secret

    return str(get_secret(SECRET_NAME) or "").strip()


# ── per-session state (shared across the parent/child process split) ───────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fast_mode (
    session_id TEXT PRIMARY KEY, mode TEXT NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tier_counts (
    session_id TEXT NOT NULL, requested TEXT NOT NULL, served TEXT NOT NULL,
    n INTEGER NOT NULL DEFAULT 0, last_at REAL NOT NULL,
    PRIMARY KEY (session_id, requested, served));
CREATE TABLE IF NOT EXISTS turn_window (
    session_id TEXT PRIMARY KEY, opened_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS redeem_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, account_alias TEXT NOT NULL,
    ok INTEGER NOT NULL, detail TEXT NOT NULL);
"""


def _db():
    from plugins.plugin_storage import plugin_db

    conn = plugin_db(PLUGIN_ID, "state.db")
    conn.executescript(_SCHEMA)
    return conn


def get_fast(session_id: str) -> str:
    if not session_id:
        return default_fast()
    conn = _db()
    try:
        row = conn.execute("SELECT mode FROM fast_mode WHERE session_id = ?", (session_id,)).fetchone()
    finally:
        conn.close()
    return row[0] if row and row[0] in FAST_MODES else default_fast()


def set_fast(session_id: str, value: str) -> None:
    if value not in FAST_MODES:
        raise ValueError(f"fast mode must be one of {', '.join(FAST_MODES)}")
    conn = _db()
    try:
        with conn:
            conn.execute(
                "INSERT INTO fast_mode(session_id, mode, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET mode = excluded.mode, updated_at = excluded.updated_at",
                (session_id, value, time.time()),
            )
    finally:
        conn.close()


def _auto_window_open(session_id: str, api_call_count: Any, now: float) -> bool:
    """``auto``: the first request of each user turn opens a window; later requests in the
    window stay fast (same contract as Hermes core's ``/fast auto``)."""
    conn = _db()
    try:
        with conn:
            if api_call_count == 1:
                conn.execute(
                    "INSERT INTO turn_window(session_id, opened_at) VALUES (?, ?) "
                    "ON CONFLICT(session_id) DO UPDATE SET opened_at = excluded.opened_at",
                    (session_id, now),
                )
                return True
            row = conn.execute("SELECT opened_at FROM turn_window WHERE session_id = ?", (session_id,)).fetchone()
    finally:
        conn.close()
    return bool(row) and now - float(row[0]) < AUTO_WINDOW_SECONDS


def wants_priority(session_id: str, api_call_count: Any = None, now: float | None = None) -> bool:
    current = get_fast(session_id)
    if current == "on":
        return True
    if current == "auto" and session_id:
        return _auto_window_open(session_id, api_call_count, time.time() if now is None else now)
    return False


def record_tier(session_id: str, requested: str | None, served: str | None) -> None:
    conn = _db()
    try:
        with conn:
            conn.execute(
                "INSERT INTO tier_counts(session_id, requested, served, n, last_at) VALUES (?, ?, ?, 1, ?) "
                "ON CONFLICT(session_id, requested, served) DO UPDATE SET n = n + 1, last_at = excluded.last_at",
                (session_id or "", requested or "none", served or UNKNOWN_TIER, time.time()),
            )
    finally:
        conn.close()


def tier_summary(session_id: str | None = None) -> dict[str, Any]:
    """Counts by (requested, served); per session when ``session_id`` is given, else totals."""
    conn = _db()
    try:
        if session_id:
            rows = conn.execute(
                "SELECT requested, served, n, last_at FROM tier_counts WHERE session_id = ?", (session_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT requested, served, SUM(n), MAX(last_at) FROM tier_counts GROUP BY requested, served"
            ).fetchall()
        last = conn.execute(
            "SELECT requested, served, last_at FROM tier_counts "
            + ("WHERE session_id = ? " if session_id else "")
            + "ORDER BY last_at DESC LIMIT 1",
            ((session_id,) if session_id else ()),
        ).fetchone()
    finally:
        conn.close()
    counts = [{"requested": r, "served": s, "n": int(n)} for r, s, n, _ in rows]
    asked = sum(c["n"] for c in counts if c["requested"] == PRIORITY)
    got = sum(c["n"] for c in counts if c["requested"] == PRIORITY and c["served"] == PRIORITY)
    # A response that carried no tier is not evidence of a downgrade
    # (gcs8/hermes-codex-lb#12); only a different, known tier is.
    unknown = sum(c["n"] for c in counts if c["requested"] == PRIORITY and c["served"] == UNKNOWN_TIER)
    return {
        "counts": counts,
        "priority_requested": asked,
        "priority_served": got,
        "priority_unknown": unknown,
        "downgraded": asked - got - unknown,
        "last": {"requested": last[0], "served": last[1], "at": last[2]} if last else None,
    }


def log_redeem(account_alias: str, ok: bool, detail: str) -> None:
    conn = _db()
    try:
        with conn:
            conn.execute(
                "INSERT INTO redeem_log(at, account_alias, ok, detail) VALUES (?, ?, ?, ?)",
                (time.time(), account_alias, 1 if ok else 0, detail[:500]),
            )
    finally:
        conn.close()


def redeem_history(limit: int = 20) -> list[dict[str, Any]]:
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT at, account_alias, ok, detail FROM redeem_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    finally:
        conn.close()
    return [{"at": a, "account": acc, "ok": bool(ok), "detail": d} for a, acc, ok, d in rows]


# ── codex-lb client ─────────────────────────────────────────────────────────────


class CodexLbError(RuntimeError):
    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


def _request(method: str, path: str, body: dict | None = None) -> Any:
    urls = base_urls()
    if not urls:
        raise CodexLbError("No codex-lb base URL configured (plugins.entries.codex-lb.settings.base_urls)")
    key = api_key()
    if not key:
        raise CodexLbError(f"No API key: set {SECRET_NAME} in the plugin's settings")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        urls[0] + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            **({"Content-Type": "application/json"} if data is not None else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:  # noqa: S310 - user-configured URL
            return json.loads(resp.read(_MAX_BODY) or b"null")
    except urllib.error.HTTPError as exc:
        raise CodexLbError(f"codex-lb answered HTTP {exc.code} for {path}", exc.code) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise CodexLbError(f"codex-lb unreachable: {type(exc).__name__}") from None


# Paths below are relative to the configured base URL, which ends in ``/v1`` (the model
# endpoint Hermes already uses), so ``/usage`` is codex-lb's ``GET /v1/usage``.


def usage() -> dict[str, Any]:
    raw = _request("GET", "/usage")
    if not isinstance(raw, Mapping):
        raise CodexLbError("unexpected /v1/usage response")
    pool = raw.get("account_pool_usage") or {}
    return {
        "requests": raw.get("request_count"),
        "total_tokens": raw.get("total_tokens"),
        "cached_input_tokens": raw.get("cached_input_tokens"),
        "cost_usd": raw.get("total_cost_usd"),
        "pool_remaining_percent": {"primary": pool.get("primary"), "secondary": pool.get("secondary")},
        "limits": list(raw.get("upstream_limits") or raw.get("limits") or []),
    }


def mask_email(email: Any) -> str:
    text = str(email or "")
    name, _, domain = text.partition("@")
    if not domain:
        return "account"
    return f"{name[:2]}…@{domain}"


def reset_credits() -> list[dict[str, Any]]:
    raw = _request("GET", "/reset-credit")
    if not isinstance(raw, list):
        raise CodexLbError("unexpected /v1/reset-credit response")
    return [
        {
            "account_id": str(c.get("account_id") or ""),
            "account": mask_email(c.get("email")),
            "redeem_id": str(c.get("redeem_id") or ""),
            "expires_at": c.get("expiredAt") or c.get("expired_at"),
        }
        for c in raw
        if isinstance(c, Mapping)
    ]


def redeem(account_id: str, redeem_id: str) -> dict[str, Any]:
    """Spend a banked reset credit. Only reachable in ``read_write`` mode, from a person."""
    if mode() != "read_write":
        raise CodexLbError("Redeeming reset credits needs mode: read_write", 403)
    match = next((c for c in reset_credits() if c["account_id"] == account_id and c["redeem_id"] == redeem_id), None)
    if match is None:
        raise CodexLbError("That reset credit is not available any more", 404)
    try:
        result = _request("POST", "/reset-credit", {"account_id": account_id, "redeem_id": redeem_id})
    except CodexLbError as exc:
        log_redeem(match["account"], False, str(exc))
        raise
    log_redeem(match["account"], True, json.dumps(result)[:500])
    return {"account": match["account"], "result": result}
