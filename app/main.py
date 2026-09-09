"""Точка входа: FastAPI (API + админка). Telegram-бот стартует отдельным процессом."""

import socket

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config.settings import get_settings

settings = get_settings()

app = FastAPI(
    title="Booking Platform API",
    version="0.1.0",
    docs_url="/api/docs" if not settings.is_prod else None,
)


def _service_available(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except OSError:
        return False


def _db_host_port() -> tuple[str, int]:
    # postgresql+psycopg://user:pass@host:port/db
    netloc = settings.database_url.split("@", 1)[-1].split("/", 1)[0]
    host, _, port = netloc.partition(":")
    return host or "localhost", int(port or 5432)


def _redis_host_port() -> tuple[str, int]:
    netloc = settings.redis_url.split("//", 1)[-1].split("/", 1)[0]
    host, _, port = netloc.partition(":")
    return host or "localhost", int(port or 6379)


@app.get("/health")
async def health() -> JSONResponse:
    db_host, db_port = _db_host_port()
    redis_host, redis_port = _redis_host_port()
    return JSONResponse(
        {
            "status": "ok",
            "env": settings.app_env,
            "postgres": _service_available(db_host, db_port),
            "redis": _service_available(redis_host, redis_port),
        }
    )
