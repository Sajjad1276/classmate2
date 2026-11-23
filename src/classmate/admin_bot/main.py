"""Entry point for the separate ClassMate management bot."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from classmate.admin_bot.router import router
from classmate.bot.middlewares import DbSessionMiddleware
from classmate.core.config import get_settings
from classmate.core.logging import configure_logging

logger = logging.getLogger(__name__)


def create_storage():
    settings = get_settings()
    if settings.redis_url:
        try:
            from aiogram.fsm.storage.redis import DefaultKeyBuilder, RedisStorage

            return RedisStorage.from_url(
                settings.redis_url,
                key_builder=DefaultKeyBuilder(with_bot_id=True),
                state_ttl=60 * 60 * 24,
                data_ttl=60 * 60 * 24,
            )
        except Exception:
            logger.exception("Admin bot RedisStorage failed; falling back to memory.")
    return MemoryStorage()


async def main() -> None:
    configure_logging()
    settings = get_settings()
    token = settings.admin_bot_token.strip()
    if not token or token.startswith("SET_ME") or token.startswith("123456"):
        raise RuntimeError("TELEGRAM_BOT_TOKEN for the management bot is not configured.")

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    storage = create_storage()
    dispatcher = Dispatcher(storage=storage)
    dispatcher.update.middleware(DbSessionMiddleware())
    dispatcher.include_router(router)
    logger.info("Starting ClassMate management bot (shared database)")
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        await bot.session.close()
        if hasattr(storage, "close"):
            await storage.close()


if __name__ == "__main__":
    asyncio.run(main())
