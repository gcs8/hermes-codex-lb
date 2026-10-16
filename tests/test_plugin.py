"""Unit tests for the codex-lb plugin. Run from a Hermes checkout's test env:

HERMES_PYTHON=<hermes>/.venv/bin/python tests/run.sh
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("CODEX_LB_PLUGIN_DIR") or Path(__file__).resolve().parents[1])
BASE = "http://lb.test:2455/v1"


def _load_package():
    spec = importlib.util.spec_from_file_location(
        "codexlb_under_test", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["codexlb_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    (tmp_path / ".hermes").mkdir()
    mod = _load_package()
    core = mod.lbcore
    cfg = {"base_urls": [BASE], "default_fast": "off", "mode": "read_only"}
    monkeypatch.setattr(core, "settings", lambda: dict(cfg))
    monkeypatch.setattr(core, "api_key", lambda: "test-key")
    yield types.SimpleNamespace(mod=mod, core=core, cfg=cfg)
    sys.modules.pop("codexlb_under_test", None)


def _request():
    return {"model": "gpt-x", "input": [], "stream": True}


def test_request_untouched_when_fast_off(plugin):
    assert plugin.mod._on_llm_request(request=_request(), base_url=BASE, session_id="s1", api_call_count=1) is None


def test_fast_on_adds_priority_only_for_codex_lb(plugin):
    plugin.core.set_fast("s1", "on")
    out = plugin.mod._on_llm_request(request=_request(), base_url=BASE + "/", session_id="s1", api_call_count=1)
    assert out["request"]["service_tier"] == "priority"
    assert (
        plugin.mod._on_llm_request(
            request=_request(), base_url="https://api.openai.com/v1", session_id="s1", api_call_count=1
        )
        is None
    )


def test_existing_tier_is_respected(plugin):
    plugin.core.set_fast("s1", "on")
    req = {**_request(), "service_tier": "flex"}
    assert plugin.mod._on_llm_request(request=req, base_url=BASE, session_id="s1", api_call_count=1) is None


def test_fast_state_is_per_session(plugin):
    plugin.core.set_fast("s1", "on")
    assert plugin.mod._on_llm_request(request=_request(), base_url=BASE, session_id="s2", api_call_count=1) is None


def test_auto_opens_on_first_call_and_expires(plugin):
    core = plugin.core
    core.set_fast("s1", "auto")
    assert core.wants_priority("s1", api_call_count=2, now=1000.0) is False  # no window yet
    assert core.wants_priority("s1", api_call_count=1, now=1000.0) is True
    assert core.wants_priority("s1", api_call_count=3, now=1000.0 + core.AUTO_WINDOW_SECONDS - 1) is True
    assert core.wants_priority("s1", api_call_count=4, now=1000.0 + core.AUTO_WINDOW_SECONDS + 1) is False


def test_default_fast_applies_to_sessions_without_a_choice(plugin):
    plugin.cfg["default_fast"] = "on"
    assert plugin.core.wants_priority("fresh") is True
    plugin.core.set_fast("fresh", "off")
    assert plugin.core.wants_priority("fresh") is False


def test_execution_records_served_tier(plugin):
    served = types.SimpleNamespace(service_tier="default")
    req = {**_request(), "service_tier": "priority"}
    result = plugin.mod._on_llm_execution(request=req, next_call=lambda r: served, base_url=BASE, session_id="s1")
    assert result is served
    summary = plugin.core.tier_summary("s1")
    assert summary["priority_requested"] == 1 and summary["priority_reported"] == 0
    assert summary["priority_reported_other"] == 1 and summary["priority_unreported"] == 0
    assert "downgraded" not in summary
    assert summary["last"]["served"] == "default"


def test_tier_summary_separates_other_tier_from_missing_tier(plugin):
    core = plugin.core
    core.record_tier("s1", "priority", "priority")
    core.record_tier("s1", "priority", "default")
    core.record_tier("s1", "priority", "default")
    core.record_tier("s1", "priority", None)  # response carried no tier
    core.record_tier("s1", None, "default")  # fast off: not counted
    summary = core.tier_summary("s1")
    assert summary["priority_requested"] == 4
    assert summary["priority_reported"] == 1
    assert summary["priority_reported_other"] == 2
    assert summary["priority_unreported"] == 1


def test_status_text_does_not_call_reported_default_a_failure(plugin):
    plugin.core.set_fast("s1", "on")
    plugin.core.record_tier("s1", "priority", "default")
    plugin.core.record_tier("s1", "priority", None)
    text = plugin.mod._status_text("s1")
    assert "downgraded" not in text and "served every priority request" not in text
    assert "reported tier for 2 priority request(s): 1 default, 1 none" in text
    assert "reports default even when fast is applied" in text


def test_execution_ignores_other_providers(plugin):
    plugin.mod._on_llm_execution(
        request=_request(), next_call=lambda r: object(), base_url="https://x/v1", session_id="s1"
    )
    assert plugin.core.tier_summary()["counts"] == []


def test_execution_never_fails_the_call_when_recording_breaks(plugin, monkeypatch):
    monkeypatch.setattr(plugin.core, "record_tier", lambda *a: (_ for _ in ()).throw(RuntimeError("db gone")))
    sentinel = object()
    assert plugin.mod._on_llm_execution(request=_request(), next_call=lambda r: sentinel, base_url=BASE) is sentinel


def test_lbfast_command(plugin, monkeypatch):
    monkeypatch.setattr(plugin.mod, "_session_id", lambda: "s9")
    assert "on for this session" in plugin.mod._lbfast("on")
    assert plugin.core.get_fast("s9") == "on"
    assert "fast mode: on" in plugin.mod._lbfast("status")
    assert plugin.mod._lbfast("bogus").startswith("Usage:")


# ── codex-lb client against a local fake ─────────────────────────────────────────


class _Fake:
    def __init__(self):
        self.calls = []
        self.credits = [
            {
                "account_id": "acc1",
                "email": "someone@example.com",
                "redeem_id": "r1",
                "expiredAt": "2026-10-20T00:00:00Z",
            }
        ]


def _serve(fake):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            fake.calls.append(("GET", self.path, self.headers.get("Authorization")))
            if self.path == "/v1/usage":
                return self._send(
                    200,
                    {
                        "request_count": 3,
                        "total_tokens": 100,
                        "cached_input_tokens": 80,
                        "total_cost_usd": 1.5,
                        "limits": [],
                        "upstream_limits": [],
                        "account_pool_usage": {"primary": 72.0, "secondary": 40.5},
                    },
                )
            if self.path == "/v1/reset-credit":
                return self._send(200, fake.credits)
            self._send(404, {})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            fake.calls.append(("POST", self.path, body))
            self._send(200, {"code": "ok", "windows_reset": 1, "redeemed_at": None})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def lb(plugin):
    fake = _Fake()
    srv = _serve(fake)
    plugin.cfg["base_urls"] = [f"http://127.0.0.1:{srv.server_address[1]}/v1"]
    yield types.SimpleNamespace(fake=fake, core=plugin.core, cfg=plugin.cfg)
    srv.shutdown()


def test_usage_maps_pool_and_sends_key(lb):
    u = lb.core.usage()
    assert u["pool_remaining_percent"] == {"primary": 72.0, "secondary": 40.5}
    assert u["requests"] == 3
    assert lb.fake.calls[0][2] == "Bearer test-key"


def test_reset_credits_mask_email(lb):
    credits = lb.core.reset_credits()
    assert credits[0]["account"] == "so…@example.com"
    assert "someone" not in json.dumps(credits)


def test_redeem_refused_in_read_only(lb):
    with pytest.raises(lb.core.CodexLbError) as err:
        lb.core.redeem("acc1", "r1")
    assert err.value.status == 403
    assert not [c for c in lb.fake.calls if c[0] == "POST"]


def test_redeem_in_read_write_posts_and_logs(lb):
    lb.cfg["mode"] = "read_write"
    out = lb.core.redeem("acc1", "r1")
    assert out["account"] == "so…@example.com"
    assert ("POST", "/v1/reset-credit", {"account_id": "acc1", "redeem_id": "r1"}) in lb.fake.calls
    assert lb.core.redeem_history()[0]["ok"] is True


def test_redeem_unknown_credit_is_refused(lb):
    lb.cfg["mode"] = "read_write"
    with pytest.raises(lb.core.CodexLbError) as err:
        lb.core.redeem("acc1", "nope")
    assert err.value.status == 404


def test_missing_configuration_is_a_clear_error(plugin):
    plugin.cfg["base_urls"] = []
    with pytest.raises(plugin.core.CodexLbError, match="base URL"):
        plugin.core.usage()
