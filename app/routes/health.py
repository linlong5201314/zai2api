"""Health endpoints + fingerprint-hiding root/404."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import __version__
from ..db import database
from ..token_pool import pool

router = APIRouter()

GENERIC_BODY = {"service": "ok"}


@router.get("/healthz")
async def healthz():
    return {"status": "ok", "version": __version__}


@router.get("/readyz")
async def readyz():
    s = await pool.summary()
    ok = (s["accounts_active"] + s["guests_active"]) > 0
    return JSONResponse({"status": "ready" if ok else "no_credentials",
                         "pool": s},
                        status_code=200 if ok else 503)


@router.get("/status")
async def status():
    return {"version": __version__, "pool": await pool.summary(),
            "usage": await database.stats()}


@router.get("/")
async def root(request: Request):
    return JSONResponse(GENERIC_BODY)
