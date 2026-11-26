import os
from unittest.mock import AsyncMock

os.environ.setdefault("BOT_TOKEN", "0000000000:TEST_TOKEN_FOR_TESTS_ONLY")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest

from classmate.bot import main as bot_main


class RedisStub:
    def __init__(self, *, set_result=True, eval_result=1):
        self.set_result = set_result
        self.eval_result = eval_result
        self.set = AsyncMock(return_value=set_result)
        self.eval = AsyncMock(return_value=eval_result)


@pytest.mark.asyncio
async def test_poller_lock_is_acquired_with_an_expiring_exclusive_lease():
    redis = RedisStub()

    await bot_main._acquire_poller_lock(redis, "replica-token")

    redis.set.assert_awaited_once_with(
        bot_main._POLLER_LOCK_KEY,
        "replica-token",
        nx=True,
        ex=bot_main._POLLER_LOCK_TTL_SECONDS,
    )


@pytest.mark.asyncio
async def test_poller_lock_release_is_owned_by_the_current_replica():
    redis = RedisStub()

    await bot_main._release_poller_lock(redis, "replica-token")

    redis.eval.assert_awaited_once_with(
        bot_main._POLLER_LOCK_RELEASE_SCRIPT,
        1,
        bot_main._POLLER_LOCK_KEY,
        "replica-token",
    )
    assert "redis.call('get', KEYS[1]) == ARGV[1]" in bot_main._POLLER_LOCK_RELEASE_SCRIPT


@pytest.mark.asyncio
async def test_poller_stops_when_its_lease_is_lost(monkeypatch):
    redis = RedisStub(eval_result=0)
    monkeypatch.setattr(bot_main.asyncio, "sleep", AsyncMock(return_value=None))

    with pytest.raises(RuntimeError, match="lease was lost"):
        await bot_main._renew_poller_lock(redis, "replica-token")

    redis.eval.assert_awaited_once_with(
        bot_main._POLLER_LOCK_RENEW_SCRIPT,
        1,
        bot_main._POLLER_LOCK_KEY,
        "replica-token",
        bot_main._POLLER_LOCK_TTL_SECONDS,
    )
