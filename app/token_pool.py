"""Dual credential pool: account JWTs (priority) + anonymous guest tokens.

Selection order for a request:
1. account tokens marked active (unlock GLM-5 family / vision / upload)
2. guest tokens (text-only, small models)
Tokens are cooled down on 429/5xx, marked invalid on 401/403, and persisted
in SQLite so restarts keep the pool state.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

import httpx

from . import config
from .converter import BROWSER_UA, upstream_headers
from .db import database

log = logging.getLogger("zai2api.pool")

COOLDOWN_429 = 60          # seconds
COOLDOWN_5XX = 15
GUEST_REFRESH_INTERVAL = config.POOL_REFRESH_INTERVAL  # seconds


class NoCredentialError(RuntimeError):
    pass


class UpstreamAuthError(RuntimeError):
    """Upstream rejected the token (401/403)."""


class UpstreamWafError(RuntimeError):
    """Upstream returned a WAF/captcha challenge (405/blocked page)."""


class TokenPool:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._started = False
        self._refresh_task: Optional[asyncio.Task] = None

    # ---- lifecycle ----

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        for t in config.ZAI_TOKENS:
            await database.upsert_token(t, "account")
        await database.recover_cooling()
        if config.ANONYMOUS_MODE:
            await self._ensure_guests(min_count=2)
        self._refresh_task = asyncio.create_task(self._refresh_loop())
        log.info("pool started: %s", await self.summary())

    async def stop(self) -> None:
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass

    async def summary(self) -> dict[str, Any]:
        toks = await database.all_tokens()
        accounts = [t for t in toks if t["kind"] == "account"]
        guests = [t for t in toks if t["kind"] == "guest"]
        return {
            "accounts": len(accounts),
            "accounts_active": sum(1 for t in accounts if t["status"] == "active"),
            "guests": len(guests),
            "guests_active": sum(1 for t in guests if t["status"] == "active"),
        }

    # ---- selection ----

    async def acquire(self, need_account: bool = False) -> dict[str, Any]:
        """Pick the next usable credential. Returns a token row dict."""
        await self._lock.acquire()
        try:
            await database.recover_cooling()
            rows = await database.active_tokens()
            accounts = [r for r in rows if r["kind"] == "account"]
            guests = [r for r in rows if r["kind"] == "guest"]

            order = ([accounts] if need_account else [accounts, guests])
            for group in order:
                if not group:
                    continue
                # least-recently-used among actives; cheap & fair
                row = min(group, key=lambda r: r.get("last_used") or 0)
                return row
            if need_account and not accounts:
                raise NoCredentialError(
                    "model requires an account JWT (ZAI_TOKENS) but pool has none")
            raise NoCredentialError("no active upstream credentials available")
        finally:
            self._lock.release()

    # ---- reporting ----

    async def report_ok(self, token: str) -> None:
        await database.bump_use(token, True)

    async def report_auth_fail(self, token: str, kind: str) -> None:
        """401/403: guest tokens get replaced, account tokens marked invalid."""
        log.warning("token %s... rejected by upstream (%s)",
                    token[:12], kind)
        if kind == "guest":
            await database.remove_token(token)
            await self._ensure_guests(min_count=1)
        else:
            await database.mark_status(token, "invalid")
        await database.bump_use(token, False)

    async def report_rate_limited(self, token: str) -> None:
        await database.mark_status(token, "cooling", COOLDOWN_429)
        await database.bump_use(token, False)

    async def report_server_error(self, token: str) -> None:
        await database.mark_status(token, "cooling", COOLDOWN_5XX)
        await database.bump_use(token, False)

    # ---- guest tokens ----

    async def _fetch_guest_token(self) -> Optional[str]:
        from .zai_client import client as zai_client
        token = await zai_client.fetch_guest_token()
        if token:
            log.info("fetched guest token %s...", token[:12])
        return token

    async def _ensure_guests(self, min_count: int = 2) -> None:
        if not config.ANONYMOUS_MODE:
            return
        rows = await database.active_tokens("guest")
        missing = min_count - len(rows)
        for _ in range(max(0, missing)):
            token = await self._fetch_guest_token()
            if token:
                await database.upsert_token(token, "guest")

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(GUEST_REFRESH_INTERVAL)
            try:
                await database.recover_cooling()
                rows = await database.active_tokens("guest")
                if len(rows) < 2:
                    await self._ensure_guests(min_count=2)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("refresh loop error: %s", e)


pool = TokenPool()
