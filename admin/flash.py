"""Редиректы с flash-сообщением (корректное URL-кодирование кириллицы)."""

from urllib.parse import quote_plus

from starlette.responses import Response


def safe_back(value) -> str | None:
    """Адрес возврата только внутри админки — ссылка не должна уводить на чужой сайт."""
    value = str(value or "").strip()
    if value.startswith("/admin/") and "\\" not in value and not any(c in value for c in "\r\n"):
        return value
    return None


def redirect(path: str, ok: str | None = None, err: str | None = None) -> Response:
    if ok:
        path += ("&" if "?" in path else "?") + "ok=" + quote_plus(ok)
    if err:
        path += ("&" if "?" in path else "?") + "err=" + quote_plus(err)
    return Response(status_code=303, headers={"Location": path})
