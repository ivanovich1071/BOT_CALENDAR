"""Точка входа: FastAPI (API + админка). Telegram-бот стартует отдельным процессом."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from admin.routes import (
    audit,
    bookings,
    calendars,
    clients,
    dashboard,
    employees,
    schedule,
    services,
    settings_page,
)
from app.api.routes import auth, google_oauth
from app.config.network import prefer_ipv4
from app.config.settings import get_settings
from app.scheduler import shutdown_scheduler, start_scheduler

logger = logging.getLogger(__name__)
settings = get_settings()

# До первого обращения к Google: иначе httplib2 упрётся в неотвечающий IPv6
if settings.prefer_ipv4:
    prefer_ipv4()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Фоновая синхронизация Google живёт столько же, сколько процесс API."""
    try:
        start_scheduler()
    except Exception:  # noqa: BLE001 — без планировщика приложение всё равно работает
        logger.exception("Не удалось запустить планировщик")
    try:
        yield
    finally:
        shutdown_scheduler()


app = FastAPI(
    title="Booking Platform API",
    version="0.2.0",
    docs_url="/api/docs" if not settings.is_prod else None,
    lifespan=lifespan,
)

app.include_router(auth.router)
app.include_router(google_oauth.router)
app.include_router(dashboard.router)
app.include_router(bookings.router)
app.include_router(schedule.router)
app.include_router(calendars.router)
app.include_router(employees.router)
app.include_router(services.router)
app.include_router(clients.router)
app.include_router(settings_page.router)
app.include_router(audit.router)


def _service_available(host: str, port: int) -> bool:
    import socket

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
    import app.scheduler as scheduler_module

    db_host, db_port = _db_host_port()
    redis_host, redis_port = _redis_host_port()
    return JSONResponse(
        {
            "status": "ok",
            "env": settings.app_env,
            "postgres": _service_available(db_host, db_port),
            "redis": _service_available(redis_host, redis_port),
            "scheduler": scheduler_module._scheduler is not None,
            "google_configured": bool(settings.google_client_id and settings.google_client_secret),
            "ai_configured": bool(settings.openrouter_api_key),
        }
    )
