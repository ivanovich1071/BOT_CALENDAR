"""Сопоставление разобранного намерения со справочниками.

Чистые функции без сети и БД. Модели доверяем ровно настолько, насколько её
ответ совпадает с тем, что реально есть в системе.
"""

import datetime as dt


def _norm(value: str) -> str:
    return " ".join(value.lower().replace("ё", "е").split())


def match_name(name: str | None, items: list[dict]) -> dict | None:
    """Точное совпадение без учёта регистра, затем однозначное вхождение.

    Модель может чуть исказить название («консультацию» вместо «Консультация»),
    но два подходящих варианта — это уже угадывание, и тогда None.
    """
    if not name or not name.strip():
        return None
    needle = _norm(name)
    exact = [item for item in items if _norm(item["name"]) == needle]
    if len(exact) == 1:
        return exact[0]
    partial = [
        item for item in items if needle in _norm(item["name"]) or _norm(item["name"]) in needle
    ]
    return partial[0] if len(partial) == 1 else None


def filter_times(times: list[str], time_from: dt.time | None, time_to: dt.time | None) -> list[str]:
    """Слоты «HH:MM» внутри окна.

    Точное время (from == to) включает сам слот. Окно «утром 09–12» слот 12:00
    не включает — это уже «днём». «До 11» тоже означает раньше 11:00.
    """
    exact = time_from is not None and time_from == time_to
    result = []
    for value in times:
        hour, minute = value.split(":")
        moment = dt.time(int(hour), int(minute))
        if time_from is not None and moment < time_from:
            continue
        if time_to is not None and (moment > time_to or (moment == time_to and not exact)):
            continue
        result.append(value)
    return result
