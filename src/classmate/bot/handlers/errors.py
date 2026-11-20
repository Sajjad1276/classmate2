"""Centralized, user-safe error handling for bot updates."""

import logging
from typing import Any

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ErrorEvent, Message

router = Router(name="errors")
logger = logging.getLogger(__name__)


async def _notify_user(update: Any, text: str) -> None:
    if isinstance(update, CallbackQuery):
        try:
            await update.answer(text, show_alert=True)
        except TelegramAPIError:
            logger.debug("Could not answer failed callback", exc_info=True)
        return
    if isinstance(update, Message):
        try:
            await update.answer(text)
        except TelegramAPIError:
            logger.debug("Could not notify failed message", exc_info=True)


@router.error()
async def on_update_error(event: ErrorEvent, state: FSMContext | None = None) -> bool:
    """Log the full exception while exposing only an actionable Persian message."""
    update = event.update
    exception = event.exception
    user = getattr(getattr(update, "from_user", None), "id", None)
    logger.exception(
        "Unhandled update error type=%s user_id=%s update_id=%s",
        type(exception).__name__,
        user,
        getattr(event, "update_id", None),
        exc_info=exception,
    )

    if state is not None:
        try:
            await state.clear()
        except Exception:
            logger.warning("Could not clear FSM state after error", exc_info=True)

    if isinstance(exception, (ValueError, TypeError, IndexError)):
        text = "ورودی قابل پردازش نیست. لطفاً دوباره از منوی ربات شروع کنید."
    elif isinstance(exception, PermissionError):
        text = "شما به این بخش دسترسی ندارید."
    else:
        text = "خطای موقت رخ داد. لطفاً چند لحظه بعد دوباره تلاش کنید."
    await _notify_user(update, text)
    return True
