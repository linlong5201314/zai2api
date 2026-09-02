"""Admin panel: login, token CRUD, logs, stats."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Any

from fastapi import APIRouter, Cookie, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .. import config
from ..db import database
from ..token_pool import pool

router = APIRouter(prefix="/admin")

_sessions: dict[str, float] = {}
SESSION_TTL = 12 * 3600


def _check_password(password: str) -> bool:
    return hmac.compare_digest(password, config.ADMIN_PASSWORD)


def _new_session() -> str:
    sid = secrets.token_urlsafe(32)
    _sessions[sid] = time.time() + SESSION_TTL
    # GC expired
    now = time.time()
    for k in [k for k, v in _sessions.items() if v < now]:
        _sessions.pop(k, None)
    return sid


def _require_session(sid: str | None) -> None:
    if not sid or sid not in _sessions or _sessions[sid] < time.time():
        raise HTTPException(status_code=401, detail="unauthorized")


@router.post("/login")
async def login(body: dict[str, Any]):
    if not _check_password(str(body.get("password", ""))):
        raise HTTPException(status_code=403, detail="wrong password")
    sid = _new_session()
    resp = JSONResponse({"ok": True})
    resp.set_cookie("zai2api_admin", sid, httponly=True, samesite="lax",
                    max_age=SESSION_TTL)
    return resp


@router.get("/session")
async def session_info(zai2api_admin: str | None = Cookie(default=None)):
    _require_session(zai2api_admin)
    return {"ok": True}


@router.get("/api/tokens")
async def list_tokens(zai2api_admin: str | None = Cookie(default=None)):
    _require_session(zai2api_admin)
    toks = await database.all_tokens()
    # never leak full JWTs to the UI
    for t in toks:
        t["token_hint"] = t["token"][:16] + "…" if len(t["token"]) > 16 else t["token"]
        t.pop("token", None)
    return {"tokens": toks}


@router.post("/api/tokens")
async def add_token(body: dict[str, Any], zai2api_admin: str | None = Cookie(default=None)):
    _require_session(zai2api_admin)
    token = str(body.get("token", "")).strip()
    kind = str(body.get("kind", "account"))
    if not token.startswith("eyJ"):
        raise HTTPException(400, "expected a Z.ai JWT (starts with eyJ)")
    await database.upsert_token(token, kind if kind in ("account", "guest")
                                else "account")
    return {"ok": True}


@router.delete("/api/tokens")
async def delete_token(body: dict[str, Any],
                       zai2api_admin: str | None = Cookie(default=None)):
    _require_session(zai2api_admin)
    await database.remove_token(str(body.get("token", "")))
    return {"ok": True}


@router.get("/api/stats")
async def stats(zai2api_admin: str | None = Cookie(default=None)):
    _require_session(zai2api_admin)
    return {"pool": await pool.summary(), "usage": await database.stats()}


@router.get("/api/logs")
async def logs(limit: int = 50, zai2api_admin: str | None = Cookie(default=None)):
    _require_session(zai2api_admin)
    return {"logs": await database.recent_logs(min(limit, 500))}


@router.get("/", response_class=HTMLResponse)
async def panel():
    from ..static_files import ADMIN_HTML
    return HTMLResponse(ADMIN_HTML)
