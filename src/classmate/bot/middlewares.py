"""Dependency injection middlewares."""

from collections.abc import Awaitable, Callable
import logging
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.db.session import SessionFactory

logger = logging.getLogger(__name__)


class DbSessionMiddleware(BaseMiddleware):
    """Inject an AsyncSession into every handler as `session`."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with SessionFactory() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                try:
                    await session.rollback()
                except Exception:
                    logger.exception("Database rollback failed after handler error")
                raise


class StartOnlyCommandMiddleware(BaseMiddleware):
    """Reject slash commands other than /start; bot features are panel-driven."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        raw_text = (getattr(event, "text", None) or getattr(event, "caption", None) or "").strip()
        if raw_text.startswith("/"):
            command = raw_text.split(maxsplit=1)[0].split("@", maxsplit=1)[0]
            if command.casefold() != "/start":
                answer = getattr(event, "answer", None)
                if answer is not None:
                    await answer(
                        "برای استفاده از امکانات ClassMate از دکمه‌های پنل استفاده کنید؛ "
                        "فقط دستور /start فعال است."
                    )
                return None
        return await handler(event, data)
