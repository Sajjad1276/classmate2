"""Securely bind a Telegram group to a ClassMate class."""

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.bot.handlers.ui import get_user
from classmate.services.permissions import connect_group_by_code

router = Router(name="group-link")


@router.message(Command("link"))
async def link_group_command(message: Message, session: AsyncSession) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("برای اتصال گروه، دستور /link را داخل همان گروه اجرا کنید.")
        return
    if message.from_user is None:
        await message.answer("فرستندهٔ دستور قابل شناسایی نیست.")
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        await message.answer("قالب دستور: /link کد_اتصال_دریافتی_از_مدیریت")
        return
    code = parts[1].strip()
    if len(code) < 16 or len(code) > 64 or any(ch.isspace() for ch in code):
        await message.answer("کد اتصال معتبر نیست. کد جدیدی از مدیر سامانه دریافت کنید.")
        return

    try:
        bot_user = await message.bot.get_me()
        bot_member = await message.bot.get_chat_member(
            chat_id=message.chat.id,
            user_id=bot_user.id,
        )
        actor_member = await message.bot.get_chat_member(
            chat_id=message.chat.id,
            user_id=message.from_user.id,
        )
    except TelegramAPIError:
        await message.answer(
            "برای اتصال، ابتدا ربات ClassMate را به مدیران گروه اضافه کنید و دوباره تلاش کنید."
        )
        return

    admin_statuses = {"administrator", "creator"}
    bot_is_admin = bot_member.status in admin_statuses
    actor_is_admin = actor_member.status in admin_statuses
    actor = await get_user(session, message.from_user, private=False)

    try:
        async with session.begin_nested():
            linked_class = await connect_group_by_code(
                session,
                code=code,
                chat_id=message.chat.id,
                chat_title=message.chat.title or str(message.chat.id),
                actor=actor,
                bot_is_admin=bot_is_admin,
                actor_is_admin=actor_is_admin,
            )
    except PermissionError as exc:
        await message.answer(str(exc))
        return
    except ValueError as exc:
        await message.answer(str(exc))
        return
    except IntegrityError:
        await message.answer(
            "این گروه قبلاً به کلاس دیگری متصل شده است؛ اتصال فعلی تغییر نکرد."
        )
        return

    await message.answer(
        "✅ این گروه با موفقیت به کلاس زیر متصل شد:\n"
        f"<b>{linked_class.name}</b>\n\n"
        "کد اتصال مصرف شد و دیگر قابل استفاده نیست."
    )
