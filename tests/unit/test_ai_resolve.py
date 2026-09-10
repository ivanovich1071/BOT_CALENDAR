"""Сопоставление ответа модели со справочниками и лимит запросов."""

import datetime as dt

import pytest

from app.ai.resolve import filter_times, match_name
from app.bot.ratelimit import SlidingWindowLimiter

SERVICES = [
    {"id": 1, "name": "Консультация"},
    {"id": 2, "name": "Стрижка мужская"},
    {"id": 3, "name": "Стрижка детская"},
]


@pytest.mark.parametrize(
    "name, expected_id",
    [
        ("Консультация", 1),
        ("консультация", 1),
        ("  КОНСУЛЬТАЦИЯ ", 1),
        ("стрижка мужская", 2),
        ("мужская", 2),
    ],
)
def test_название_находится(name, expected_id):
    assert match_name(name, SERVICES)["id"] == expected_id


@pytest.mark.parametrize("name", ["стрижка", "маникюр", "", None])
def test_двусмысленное_или_чужое_не_угадывается(name):
    assert match_name(name, SERVICES) is None


def test_ё_и_е_не_различаются():
    assert match_name("Семен", [{"id": 1, "name": "Семён"}])["id"] == 1


TIMES = ["09:00", "10:00", "11:00", "12:00", "15:00", "17:00", "18:00"]


def t(value):
    return dt.time.fromisoformat(value)


@pytest.mark.parametrize(
    "time_from, time_to, expected",
    [
        (None, None, TIMES),
        (t("09:00"), t("12:00"), ["09:00", "10:00", "11:00"]),   # утром: 12:00 — уже день
        (t("15:00"), t("15:00"), ["15:00"]),                     # точное время
        (t("14:00"), None, ["15:00", "17:00", "18:00"]),         # после 14
        (None, t("11:00"), ["09:00", "10:00"]),                  # до 11
        (t("19:00"), t("21:00"), []),                            # вечером пусто
    ],
)
def test_окно_времени(time_from, time_to, expected):
    assert filter_times(TIMES, time_from, time_to) == expected


def test_лимит_запросов_скользящим_окном():
    now = [0.0]
    limiter = SlidingWindowLimiter(limit=2, window_seconds=60, clock=lambda: now[0])
    assert limiter.allow(1) and limiter.allow(1)
    assert not limiter.allow(1)
    assert limiter.allow(2)  # у другого клиента свой счёт
    now[0] = 61
    assert limiter.allow(1)
