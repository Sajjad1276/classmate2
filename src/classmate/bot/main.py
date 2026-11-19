import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from classmate.bot.handlers import setup_routers
from classmate.bot.middlewares import DbSessionMiddleware
from classmate.core.config import get_settings
from classmate.core.logging import configure_logging

logger = logging.getLogger(__name__)


def create_storage():
    """Prefer Redis when available, fall back to MemoryStorage."""
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

    # Global middleware — injects session into every handler
    dp.update.middleware(DbSessionMiddleware())

    # Domain routers
    dp.include_router(setup_routers())

    logger.info("Starting ClassMate 2 bot")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        if hasattr(storage, "close"):
            await storage.close()


if __name__ == "__main__":
    asyncio.run(main())
