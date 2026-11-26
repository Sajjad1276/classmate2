"""Dependency injection middlewares."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
import logging
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.db.models import User
from classmate.db.session import SessionFactory

logger = logging.getLogger(__name__)


async def record_private_activity(session: AsyncSession, event: TelegramObject) -> None:
    """Track actual private-chat activity without storing message content."""
    message = getattr(event, "message", None)
    if message is None and hasattr(event, "chat"):
        message = event
    callback = getattr(event, "callback_query", None)

    if callback is not None:
        sender = callback.from_user
        chat = getattr(callback.message, "chat", None)
    elif message is not None:
        sender = getattr(message, "from_user", None)
        chat = getattr(message, "chat", None)
    else:
        return

    if sender is None or chat is None or getattr(chat, "type", None) != "private":
        return

    await session.execute(
        update(User)
        .where(
            User.telegram_id == sender.id,
            User.private_chat_started.is_(True),
            User.is_active.is_(True),
        )
        .values(last_seen_at=datetime.now(UTC))
    )


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
                await record_private_activity(session, event)
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
