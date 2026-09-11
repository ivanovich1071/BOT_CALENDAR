"""«Было → стало» для журнала аудита: видно, что именно поменяли в карточке."""

# Длинные тексты (описания, статьи) в журнал целиком не пишем
MAX_VALUE_CHARS = 200


def _short(value):
    if isinstance(value, str) and len(value) > MAX_VALUE_CHARS:
        return value[:MAX_VALUE_CHARS] + "…"
    return value


def changed_fields(before: dict, after: dict) -> dict:
    """Только изменившиеся поля. Значения должны быть JSON-совместимыми."""
    return {
        key: {"было": _short(before.get(key)), "стало": _short(value)}
        for key, value in after.items()
        if before.get(key) != value
    }
