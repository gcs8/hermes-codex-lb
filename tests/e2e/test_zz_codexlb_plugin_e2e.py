"""End-to-end: the real codex-lb plugin in real Hermes processes (CLI and tui_gateway, with
and without dashboard.turn_isolation) against Hermes's fake Responses server.

Copy into a Hermes checkout's ``tests/e2e/core/providers/`` and run with
``scripts/run_tests.sh --include-integration <file>`` (tests/e2e/run.sh does this).
Proves: /lbfast on flips the wire tier, off leaves it alone, and the served tier is recorded,
including when the turn runs in the compute-host child and the command in the parent.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest
from tests.e2e.core.providers._openai_helpers import Home, responses_provider_config
from tests.e2e.core.providers._openai_tui import TuiGateway
from tests.fakes.providers import openai_responses as fake
from tests.fakes.providers.openai_responses import FakeResponsesServer, Message, Turn

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="subprocess harness is Linux-gated")

PLUGIN_SRC = Path(__file__).with_name("codex_lb_plugin_src")


@pytest.fixture(autouse=True)
def _served_default(monkeypatch):
    real = fake._response
    monkeypatch.setattr(fake, "_response", lambda *a, **k: real(*a, **k).model_copy(update={"service_tier": "default"}))


def _home(tmp_path, base_url: str, *, isolation: bool) -> Home:
    h = Home(tmp_path)
    cfg = responses_provider_config(base_url)
    cfg["plugins"] = {
        "enabled": ["codex-lb"],
        "entries": {"codex-lb": {"settings": {"base_urls": [base_url], "default_fast": "off"}}},
    }
    cfg["dashboard"] = {"turn_isolation": isolation}
    h.write(cfg)
    shutil.copytree(PLUGIN_SRC, h.hermes_home / "plugins" / "codex-lb")
    return h


def _tier_rows(h: Home) -> list[tuple]:
    db = h.hermes_home / "plugin-data" / "codex-lb" / "state.db"
    if not db.exists():
        return []
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT requested, served, n FROM tier_counts ORDER BY requested").fetchall()


def _turn(gw, sid: str, text: str, expect: str) -> None:
    """``gw.turn`` that tolerates Hermes queueing the submit while an isolated turn settles
    (NousResearch/hermes-agent#135366): a queued prompt still runs, so wait for its reply."""
    start = len(gw.events)
    reply = gw.call("prompt.submit", {"session_id": sid, "text": text})
    assert reply.get("status") in ("streaming", "queued"), reply
    gw.wait_event(
        lambda e: (
            e.get("type") == "message.complete"
            and e.get("session_id") == sid
            and expect in json.dumps(e.get("payload") or {})
        ),
        120,
        start,
    )
    gw.wait_event(
        lambda e: (
            e.get("type") == "session.info"
            and e.get("session_id") == sid
            and (e.get("payload") or {}).get("running") is False
        ),
        60,
        start,
    )


@pytest.mark.parametrize("isolation", [False, True], ids=["inproc", "isolated"])
def test_lbfast_controls_wire_tier_and_records_served(tmp_path, isolation):
    script = [Turn([Message("ONE")]), Turn([Message("TWO")]), Turn([Message("THREE")])]
    with FakeResponsesServer(script) as srv:
        h = _home(tmp_path, srv.base_url, isolation=isolation)
        gw = TuiGateway(h)
        try:
            sid = gw.call("session.create", {"cols": 120})["session_id"]
            _turn(gw, sid, "first", "ONE")  # default off
            on = gw.call("slash.exec", {"session_id": sid, "command": "/lbfast on"})
            _turn(gw, sid, "second", "TWO")  # fast on
            status = gw.call("slash.exec", {"session_id": sid, "command": "/lbfast status"})
            gw.call("slash.exec", {"session_id": sid, "command": "/lbfast off"})
            _turn(gw, sid, "third", "THREE")  # off again
        finally:
            gw.close()
            tail = gw.tail(2000)
        tiers = [b.get("service_tier") for b in srv.main_requests()]
        invalid = srv.invalid_requests()
    # Hermes 0.22.0 replays assistant history in a form the fake's SDK schema rejects from the
    # third request on, with or without this plugin (control run). Only tier-related errors count.
    assert not [e for _, e in invalid if "service_tier" in e], invalid
    assert "on for this session" in json.dumps(on), (on, tail)
    assert tiers == [None, "priority", None], (tiers, tail)
    assert "reported tier for 1 priority request(s): 1 default" in json.dumps(status), status
    assert ("priority", "default", 1) in _tier_rows(h), _tier_rows(h)
