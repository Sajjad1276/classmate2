import asyncio
import logging
import secrets

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from redis.asyncio import Redis

from classmate.bot.command_menu import setup_default_commands
from classmate.bot.handlers import setup_routers
from classmate.bot.middlewares import DbSessionMiddleware, StartOnlyCommandMiddleware
from classmate.core.config import get_settings
from classmate.core.logging import configure_logging

logger = logging.getLogger(__name__)

_POLLER_LOCK_KEY = "classmate2:telegram:poller-lock"
_POLLER_LOCK_TTL_SECONDS = 60
_POLLER_LOCK_ACQUIRE_TIMEOUT_SECONDS = 90
_POLLER_LOCK_RETRY_SECONDS = 1
_POLLER_LOCK_RENEW_SECONDS = 15
_POLLER_LOCK_RENEW_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""
_POLLER_LOCK_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


def create_storage():
    settings = get_settings()
    if settings.redis_url:
        try:
            from aiogram.fsm.storage.redis import DefaultKeyBuilder, RedisStorage

            storage = RedisStorage.from_url(
                settings.redis_url,
                key_builder=DefaultKeyBuilder(with_bot_id=True),
                state_ttl=60 * 60 * 24,
                data_ttl=60 * 60 * 24,
            )
            logger.info("Using RedisStorage for FSM")
            return storage
        except Exception:
            logger.exception("Failed to connect to Redis, falling back to MemoryStorage")

    logger.warning("Using MemoryStorage — FSM state will be lost on restart")
    return MemoryStorage()


async def _acquire_poller_lock(redis: Redis, token: str) -> None:
    """Wait for the previous replica to hand over the exclusive Telegram polling lease."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _POLLER_LOCK_ACQUIRE_TIMEOUT_SECONDS
    next_notice = loop.time()

    while True:
        acquired = await redis.set(
            _POLLER_LOCK_KEY,
            token,
            nx=True,
            ex=_POLLER_LOCK_TTL_SECONDS,
        )
        if acquired:
            logger.info("Telegram polling lease acquired.")
            return

        now = loop.time()
        if now >= deadline:
            raise RuntimeError(
                "Timed out waiting for the previous ClassMate Telegram poller to stop."
            )
        if now >= next_notice:
            logger.info("Another ClassMate replica owns Telegram polling; waiting for handover.")
            next_notice = now + 10
        await asyncio.sleep(_POLLER_LOCK_RETRY_SECONDS)


async def _renew_poller_lock(redis: Redis, token: str) -> None:
    while True:
        await asyncio.sleep(_POLLER_LOCK_RENEW_SECONDS)
        renewed = await redis.eval(
            _POLLER_LOCK_RENEW_SCRIPT,
            1,
            _POLLER_LOCK_KEY,
            token,
            _POLLER_LOCK_TTL_SECONDS,
        )
        if not renewed:
            raise RuntimeError("Telegram polling lease was lost; stopping this poller.")


async def _release_poller_lock(redis: Redis, token: str) -> None:
    released = await redis.eval(
        _POLLER_LOCK_RELEASE_SCRIPT,
        1,
        _POLLER_LOCK_KEY,
        token,
    )
    if not released:
        logger.warning("Telegram polling lease was already released or replaced.")


async def _run_polling(dispatcher: Dispatcher, bot: Bot) -> None:
    await dispatcher.start_polling(
        bot,
        allowed_updates=dispatcher.resolve_used_update_types(),
        handle_signals=True,
        close_bot_session=False,
    )


async def main() -> None:
    configure_logging()
    settings = get_settings()

    token = settings.bot_token.strip()
    if not token or token.startswith("SET_ME") or token.startswith("123456"):
        raise RuntimeError(
            "BOT_TOKEN is not configured. Set a real token from @BotFather in .env"
        )

    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    storage = create_storage()
    dp = Dispatcher(storage=storage)
    dp.update.middleware(DbSessionMiddleware())
    dp.message.middleware(StartOnlyCommandMiddleware())
    dp.include_router(setup_routers())

    lock_client: Redis | None = None
    lock_token: str | None = None
    lock_acquired = False
    polling_task: asyncio.Task | None = None
    renewal_task: asyncio.Task | None = None

    try:
        await setup_default_commands(bot)

        if settings.redis_url:
            lock_client = Redis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            await lock_client.ping()
            lock_token = secrets.token_urlsafe(32)
            await _acquire_poller_lock(lock_client, lock_token)
            lock_acquired = True
        else:
            logger.warning(
                "REDIS_URL is not configured; Telegram polling is running without a "
                "distributed single-poller lease."
            )

        logger.info("Starting ClassMate 2 bot")
        polling_task = asyncio.create_task(_run_polling(dp, bot))

        if lock_client is not None and lock_token is not None:
            renewal_task = asyncio.create_task(_renew_poller_lock(lock_client, lock_token))
            done, _ = await asyncio.wait(
                {polling_task, renewal_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if renewal_task in done:
                await renewal_task
            await polling_task
        else:
            await polling_task
    finally:
        for task in (polling_task, renewal_task):
            if task is not None and not task.done():
                task.cancel()
        pending_tasks = [
            task
            for task in (polling_task, renewal_task)
            if task is not None
        ]
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)

        if lock_client is not None and lock_token is not None and lock_acquired:
            try:
                await _release_poller_lock(lock_client, lock_token)
            except Exception:
                logger.exception("Failed to release Telegram polling lease.")

        try:
            await bot.session.close()
        finally:
            if hasattr(storage, "close"):
                await storage.close()
            if lock_client is not None:
                await lock_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
