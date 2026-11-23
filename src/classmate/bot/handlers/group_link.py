"""Secure group binding through the primary ClassMate 2 bot."""

import logging

from aiogram import F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.bot.handlers.ui import get_user
from classmate.core.config import get_settings
from classmate.services.permissions import connect_group_by_code

router = Router(name="group-link")
logger = logging.getLogger(__name__)


@router.message(F.chat.type.in_({"group", "supergroup"}), Command("connect"))
async def connect_class_group(message, session: AsyncSession) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("قالب اتصال: /connect کد_دریافتی_از_ربات_مدیریت")
        return

    user = await get_user(session, message.from_user, private=False)
    settings = get_settings()
    if message.from_user.id in settings.bootstrap_admin_id_set:
        user.is_platform_admin = True

    try:
        bot_user = await message.bot.get_me()
        bot_member = await message.bot.get_chat_member(message.chat.id, bot_user.id)
        actor_member = await message.bot.get_chat_member(message.chat.id, message.from_user.id)
    except Exception:
        logger.exception("Failed to verify group administrator status")
        await message.answer("بررسی مدیران گروه ممکن نشد. دسترسی ربات را در گروه بررسی کنید.")
        return

    bot_is_admin = bot_member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}
    actor_is_admin = actor_member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}
    try:
        cls = await connect_group_by_code(
            session,
            code=parts[1],
            chat_id=message.chat.id,
            chat_title=message.chat.title,
            actor=user,
            bot_is_admin=bot_is_admin,
            actor_is_admin=actor_is_admin,
        )
    except (PermissionError, ValueError) as exc:
        await message.answer(str(exc))
        return

    await message.answer(
        f"✅ این گروه به کلاس «{cls.name}» متصل شد.\n"
        "اکنون کلاس فعال است و پنل گروه در دسترس اعضای مجاز قرار می‌گیرد."
    )
