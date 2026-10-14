"""PROBE (not for upstream): can a user plugin set ``service_tier`` on a codex_responses
request and read the served tier back, in every runtime Hermes Desktop uses?

Runtimes: CLI one-shot, tui_gateway in-process, tui_gateway with dashboard.turn_isolation
(compute-host child). The fake server echoes ``service_tier="default"`` on every response,
the way codex-lb 1.25.0-beta.9 downgrades priority requests.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

from tests.e2e.core.providers._openai_helpers import Home, oneshot, responses_provider_config
from tests.e2e.core.providers._openai_tui import TuiGateway
from tests.fakes.providers import openai_responses as fake
from tests.fakes.providers.openai_responses import FakeResponsesServer, Message, Turn

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="subprocess harness is Linux-gated")

PLUGIN = "codexlbprobe"

_PLUGIN_SRC = '''
import json, os
LOG = {log!r}

def _w(rec):
    with open(LOG, "a") as fh:
        fh.write(json.dumps(rec) + "\\n")

def register(ctx):
    def on_request(request=None, base_url=None, provider=None, session_id=None, api_mode=None, **kw):
        _w({{"ev": "request", "pid": os.getpid(), "child": os.environ.get("HERMES_COMPUTE_HOST_CHILD"),
            "base_url": base_url, "provider": provider, "api_mode": api_mode, "session_id": session_id,
            "tier_in": (request or {{}}).get("service_tier")}})
        out = dict(request or {{}})
        out["service_tier"] = "priority"
        return {{"request": out, "source": "codexlb-probe", "reason": "probe"}}

    def on_execution(request=None, next_call=None, **kw):
        result = next_call(request)
        _w({{"ev": "exec", "pid": os.getpid(), "child": os.environ.get("HERMES_COMPUTE_HOST_CHILD"),
            "tier_req": (request or {{}}).get("service_tier"), "result_type": type(result).__name__,
            "served": getattr(result, "service_tier", None), "session_id": kw.get("session_id")}})
        return result

    def lbprobe(arg):
        from gateway.session_context import get_session_env
        _w({{"ev": "command", "pid": os.getpid(), "child": os.environ.get("HERMES_COMPUTE_HOST_CHILD"),
            "arg": arg, "HERMES_SESSION_ID": get_session_env("HERMES_SESSION_ID"),
            "HERMES_SESSION_KEY": get_session_env("HERMES_SESSION_KEY")}})
        return "probe-ok"

    ctx.register_command("lbprobe", lbprobe, description="probe")
    ctx.register_middleware("llm_request", on_request)
    ctx.register_middleware("llm_execution", on_execution)
'''


@pytest.fixture(autouse=True)
def _served_default(monkeypatch):
    real = fake._response

    def with_tier(*a, **k):
        return real(*a, **k).model_copy(update={"service_tier": "default"})

    monkeypatch.setattr(fake, "_response", with_tier)


def _home(tmp_path, base_url: str, *, isolation: bool) -> tuple[Home, object]:
    h = Home(tmp_path)
    cfg = responses_provider_config(base_url)
    cfg["plugins"] = {"enabled": [PLUGIN]}
    cfg["dashboard"] = {"turn_isolation": isolation}
    h.write(cfg)
    log = tmp_path / "probe.jsonl"
    pdir = h.hermes_home / "plugins" / PLUGIN
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "plugin.yaml").write_text(
        f"name: {PLUGIN}\nversion: 0.0.1\ndescription: probe\nprovides_middleware: [llm_request, llm_execution]\nprovides_commands: [lbprobe]\n",
        encoding="utf-8")
    (pdir / "__init__.py").write_text(_PLUGIN_SRC.format(log=str(log)), encoding="utf-8")
    return h, log


def _records(log) -> list[dict]:
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


def _report(name, srv, log, extra=""):
    recs = _records(log)
    tiers = [b.get("service_tier") for b in srv.main_requests()]
    print(f"\n### {name}: wire service_tier={tiers} records={json.dumps(recs)} {extra}")
    with open("" + os.environ.get("CODEXLB_PROBE_OUT", "/tmp/codexlb-probe-out.jsonl") + "", "a") as fh:
        fh.write(json.dumps({"name": name, "wire": tiers, "records": recs, "extra": extra}) + "\n")
    return recs, tiers


def test_cli_oneshot(tmp_path):
    with FakeResponsesServer([Turn([Message("CLI-OK")])]) as srv:
        h, log = _home(tmp_path, srv.base_url, isolation=False)
        run = oneshot(h, "hello")
        recs, tiers = _report("cli", srv, log, run.stdout[-200:])
    assert "CLI-OK" in run.stdout, run.describe()
    assert tiers and tiers[0] == "priority"
    assert any(r["ev"] == "exec" for r in recs)


@pytest.mark.parametrize("isolation,slash", [(False, True), (True, False), (True, True)],
                         ids=["inproc-slash", "iso-noslash", "iso-slash"])
def test_tui_gateway(tmp_path, isolation, slash):
    import time
    with FakeResponsesServer([Turn([Message("TUI-OK")]), Turn([Message("TUI-TWO")])]) as srv:
        h, log = _home(tmp_path, srv.base_url, isolation=isolation)
        gw = TuiGateway(h)
        info = {}
        try:
            sid = gw.call("session.create", {"cols": 120})["session_id"]
            info["t1"] = gw.turn(sid, "hello")
            if slash:
                info["cmd"] = gw.call("slash.exec", {"session_id": sid, "command": "/lbprobe x"})
            # settle: poll until the parent reports idle (isolated turn.done can trail message.complete)
            for _ in range(100):
                st = gw.call("session.info", {"session_id": sid}) if False else None
                break
            time.sleep(1.5)
            r2 = gw.call("prompt.submit", {"session_id": sid, "text": "again"})
            info["submit2"] = r2
            gw.wait_event(lambda e: e.get("type") == "message.complete" and e.get("session_id") == sid and "TUI-TWO" in json.dumps(e), 90)
            info["t2"] = "ok"
        except Exception as exc:
            info["error"] = str(exc)[:600]
        finally:
            gw.close()
        recs, tiers = _report(f"tui iso={isolation} slash={slash}", srv, log, json.dumps(info))
    assert "error" not in info, info
