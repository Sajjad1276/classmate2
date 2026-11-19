from html import escape

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.bot.keyboards import main_menu
from classmate.core.config import get_settings
from classmate.db.models import User
from classmate.services.users import ensure_user

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()

    settings = get_settings()
    user = await ensure_user(
        session,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name or message.from_user.first_name or "User",
        private_chat_started=message.chat.type == "private",
    )

    if message.from_user.id in settings.bootstrap_admin_id_set:
        user.is_platform_admin = True

    name = escape(message.from_user.first_name or "")
    text = (
        f"سلام {name} 👋\n\n"
        "به <b>ClassMate 2</b> خوش آمدی.\n"
        "اینجا اطلاعات کلاس‌ها، تکالیف، امتحانات و گزارش‌های روزانه "
        "به صورت ساختاریافته و با تأیید افراد مورد اعتماد نگهداری می‌شود.\n\n"
        "اطلاعات تا زمان تأیید، به عنوان قطعی نمایش داده نمی‌شوند."
    )

    if user.is_platform_admin:
        text += "\n\n🔑 دسترسی مدیر کل سامانه برای تو فعال است."

    await message.answer(text, reply_markup=main_menu())
