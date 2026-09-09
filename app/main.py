"""Точка входа: FastAPI (API + админка). Telegram-бот стартует отдельным процессом."""

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
from app.config.settings import get_settings

settings = get_settings()

app = FastAPI(
    title="Booking Platform API",
    version="0.1.0",
    docs_url="/api/docs" if not settings.is_prod else None,
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
