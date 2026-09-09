"""Живучесть старта бота при обрывах связи с Telegram."""

import pytest
from aiogram.exceptions import TelegramNetworkError

from app.bot.main import with_retry


@pytest.mark.asyncio
async def test_повтор_после_обрыва_связи():
    """Связь с api.telegram.org прерывистая — первая неудача не должна ронять бот."""
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise TelegramNetworkError(method=None, message="обрыв")
        return "ok"

    assert await with_retry(flaky, "тест", attempts=5, backoff=0) == "ok"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_попытки_не_бесконечные():
    async def always_broken():
        raise TelegramNetworkError(method=None, message="обрыв")

    with pytest.raises(TelegramNetworkError):
        await with_retry(always_broken, "тест", attempts=3, backoff=0)


@pytest.mark.asyncio
async def test_успешный_вызов_не_повторяется():
    calls = {"n": 0}

    async def fine():
        calls["n"] += 1
        return 42

    assert await with_retry(fine, "тест", attempts=3, backoff=0) == 42
    assert calls["n"] == 1
