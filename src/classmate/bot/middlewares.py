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
