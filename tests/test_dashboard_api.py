"""The dashboard backend routes, loaded the way Hermes's dashboard loads them (by file path)."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

PLUGIN = Path(os.environ.get("CODEX_LB_PLUGIN_DIR") or Path(__file__).resolve().parents[1])


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    (tmp_path / ".hermes").mkdir()
    spec = importlib.util.spec_from_file_location(
        "codexlb_dashboard_under_test", PLUGIN / "dashboard" / "plugin_api.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    core = mod.lbcore
    cfg = {"base_urls": ["http://lb.test/v1"], "mode": "read_only"}
    monkeypatch.setattr(core, "settings", lambda: dict(cfg))
    monkeypatch.setattr(core, "api_key", lambda: "k")
    monkeypatch.setattr(core, "usage", lambda: {"pool_remaining_percent": {"primary": 50.0, "secondary": 80.0}})
    monkeypatch.setattr(core, "reset_credits", lambda: [{"account_id": "a", "account": "so…@x.com", "redeem_id": "r"}])
    app = FastAPI()
    app.include_router(mod.router, prefix="/api/plugins/codex-lb")
    return TestClient(app), core, cfg


def test_status_and_usage(api):
    client, core, _ = api
    core.record_tier("s", "priority", "default")
    status = client.get("/api/plugins/codex-lb/status").json()
    assert status["configured"] is True and status["mode"] == "read_only"
    assert status["tiers"]["downgraded"] == 1
    assert client.get("/api/plugins/codex-lb/usage").json()["pool_remaining_percent"]["primary"] == 50.0


def test_unconfigured_status(api, monkeypatch):
    client, core, cfg = api
    cfg["base_urls"] = []
    assert client.get("/api/plugins/codex-lb/status").json()["configured"] is False


def test_redeem_refused_read_only(api):
    client, _, _ = api
    resp = client.post("/api/plugins/codex-lb/reset-credits/redeem", json={"account_id": "a", "redeem_id": "r"})
    assert resp.status_code == 403
    credits = client.get("/api/plugins/codex-lb/reset-credits").json()
    assert credits["can_redeem"] is False and credits["credits"][0]["account"] == "so…@x.com"


def test_upstream_error_maps_to_502(api, monkeypatch):
    client, core, _ = api

    def boom():
        raise core.CodexLbError("codex-lb unreachable: URLError")

    monkeypatch.setattr(core, "usage", boom)
    resp = client.get("/api/plugins/codex-lb/usage")
    assert resp.status_code == 502 and "unreachable" in resp.json()["detail"]
