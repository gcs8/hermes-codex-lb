"""codex-lb plugin: dashboard backend, mounted at ``/api/plugins/codex-lb/``.

The desktop half calls these through ``ctx.rest``. The codex-lb API key stays here, in the
gateway process; the renderer only receives numbers and masked account names.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


def _load_core():
    # The dashboard imports this file on its own (not as part of the plugin package), so load
    # the sibling ``lbcore.py`` by path under a name private to this plugin.
    path = Path(__file__).resolve().parent.parent / "lbcore.py"
    spec = importlib.util.spec_from_file_location("codex_lb_plugin_lbcore", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"codex-lb plugin: cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lbcore = _load_core()
router = APIRouter()


def _fail(exc: Exception) -> HTTPException:
    status = getattr(exc, "status", 0)
    return HTTPException(status_code=status if status in (403, 404) else 502, detail=str(exc))


@router.get("/status")
async def status() -> dict:
    cfg = lbcore.settings()
    return {
        "configured": bool(lbcore.base_urls(cfg)) and bool(lbcore.api_key()),
        "mode": lbcore.mode(cfg),
        "default_fast": lbcore.default_fast(cfg),
        "tiers": lbcore.tier_summary(),
    }


@router.get("/usage")
async def usage() -> dict:
    try:
        return lbcore.usage()
    except lbcore.CodexLbError as exc:
        raise _fail(exc) from None


@router.get("/reset-credits")
async def reset_credits() -> dict:
    try:
        credits = lbcore.reset_credits()
    except lbcore.CodexLbError as exc:
        raise _fail(exc) from None
    return {"credits": credits, "can_redeem": lbcore.mode() == "read_write", "history": lbcore.redeem_history()}


class RedeemBody(BaseModel):
    account_id: str
    redeem_id: str


@router.post("/reset-credits/redeem")
async def redeem(body: RedeemBody) -> dict:
    try:
        return lbcore.redeem(body.account_id, body.redeem_id)
    except lbcore.CodexLbError as exc:
        raise _fail(exc) from None
