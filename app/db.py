"""SQLite persistence: token pool + request log."""
from __future__ import annotations

import json
import time
from typing import Any, Optional

import aiosqlite

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
    token      TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,            -- 'account' | 'guest'
    status     TEXT NOT NULL DEFAULT 'active',  -- active|invalid|cooling
    cooling_until REAL DEFAULT 0,
    uses       INTEGER DEFAULT 0,
    errors     INTEGER DEFAULT 0,
    last_used  REAL DEFAULT 0,
    added_at   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS request_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL NOT NULL,
    model      TEXT,
    key_hint   TEXT,
    token_kind TEXT,
    status     TEXT,                     -- ok|error
    http_status INTEGER,
    ttft_ms    REAL,
    latency_ms REAL,
    prompt_t   INTEGER,
    completion_t INTEGER,
    error      TEXT,
    curl       TEXT
);
CREATE INDEX IF NOT EXISTS idx_log_ts ON request_log(ts);
"""


class Database:
    def __init__(self, path: str | None = None):
        self.path = str(path or config.DB_PATH)
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "Database not connected"
        return self._db

    # ---- tokens ----

    async def upsert_token(self, token: str, kind: str) -> None:
        await self.db.execute(
            "INSERT INTO tokens(token, kind, added_at) VALUES(?,?,?) "
            "ON CONFLICT(token) DO UPDATE SET kind=excluded.kind",
            (token, kind, time.time()))
        await self.db.commit()

    async def remove_token(self, token: str) -> None:
        await self.db.execute("DELETE FROM tokens WHERE token=?", (token,))
        await self.db.commit()

    async def all_tokens(self) -> list[dict[str, Any]]:
        cur = await self.db.execute(
            "SELECT token, kind, status, cooling_until, uses, errors, last_used "
            "FROM tokens ORDER BY kind, added_at")
        return [dict(r) for r in await cur.fetchall()]

    async def active_tokens(self, kind: str | None = None) -> list[dict[str, Any]]:
        if kind:
            cur = await self.db.execute(
                "SELECT * FROM tokens WHERE kind=? AND status='active'", (kind,))
        else:
            cur = await self.db.execute(
                "SELECT * FROM tokens WHERE status='active'")
        return [dict(r) for r in await cur.fetchall()]

    async def mark_status(self, token: str, status: str,
                          cooling_seconds: float = 0) -> None:
        await self.db.execute(
            "UPDATE tokens SET status=?, cooling_until=? WHERE token=?",
            (status, time.time() + cooling_seconds, token))
        await self.db.commit()

    async def bump_use(self, token: str, ok: bool) -> None:
        await self.db.execute(
            "UPDATE tokens SET uses=uses+1, last_used=?, "
            "errors=errors+? WHERE token=?",
            (time.time(), 0 if ok else 1, token))
        await self.db.commit()

    async def recover_cooling(self) -> None:
        await self.db.execute(
            "UPDATE tokens SET status='active', cooling_until=0 "
            "WHERE status='cooling' AND cooling_until<=?", (time.time(),))
        await self.db.commit()

    # ---- request log ----

    async def log_request(self, **kw: Any) -> None:
        cols = ("ts", "model", "key_hint", "token_kind", "status", "http_status",
                "ttft_ms", "latency_ms", "prompt_t", "completion_t", "error",
                "curl")
        row = [kw.get(c) for c in cols]
        await self.db.execute(
            f"INSERT INTO request_log({','.join(cols)}) "
            f"VALUES({','.join('?' * len(cols))})", row)
        await self.db.commit()

    async def recent_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        cur = await self.db.execute(
            "SELECT * FROM request_log ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in await cur.fetchall()]

    async def stats(self) -> dict[str, Any]:
        cur = await self.db.execute(
            "SELECT COUNT(*) AS n, "
            "SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) AS ok, "
            "AVG(ttft_ms) AS ttft, AVG(latency_ms) AS lat "
            "FROM request_log WHERE ts > ?", (time.time() - 86400,))
        r = dict(await cur.fetchone())
        cur2 = await self.db.execute(
            "SELECT kind, COUNT(*) AS n FROM tokens GROUP BY kind")
        pools = {row["kind"]: row["n"] for row in await cur2.fetchall()}
        return {"requests_24h": r["n"] or 0, "ok_24h": r["ok"] or 0,
                "avg_ttft_ms": round(r["ttft"] or 0, 1),
                "avg_latency_ms": round(r["lat"] or 0, 1),
                "tokens": pools}


# single shared instance
database = Database()
