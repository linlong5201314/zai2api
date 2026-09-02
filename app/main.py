"""zai2api entrypoint: FastAPI app assembly + uvicorn launch."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import config
from .db import database
from .routes import admin, health, openai, responses
from .token_pool import pool
from .zai_client import client as zai_client

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.connect()
    await pool.start()
    await zai_client.start()
    yield
    await zai_client.stop()
    await pool.stop()
    await database.close()


app = FastAPI(title="zai2api", docs_url=None, redoc_url=None,
              openapi_url=None, lifespan=lifespan)

app.include_router(openai.router)
app.include_router(responses.router)
app.include_router(health.router)
app.include_router(admin.router)


@app.exception_handler(404)
async def not_found(request, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse({"service": "ok"}, status_code=404)


def main() -> None:
    import uvicorn
    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT,
                log_level=config.LOG_LEVEL.lower(), access_log=False)


if __name__ == "__main__":
    main()
