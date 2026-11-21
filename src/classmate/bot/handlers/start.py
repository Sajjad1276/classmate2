from html import escape
from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession
from classmate.bot.handlers.ui import get_user, menu_for

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    name = escape(message.from_user.first_name or "")
    username = escape(f"@{message.from_user.username}" if message.from_user.username else "بدون نام کاربری")
    await message.answer(
        f"• درود بیکران 🕊 {name}، به ربات ClassMatePro خوش آمدید.\n\n"
        f"📇 - در اینجا میتونید امور مدرسه رو به صورت حرفهای و پیشرفته مدیریت کنید.\n\n"
        f"• آیدی شما : {username}\n\n"
        "🪎جهت ادامه دستور مورد نظر رو از پنل زیر وارد کنید:",
        reply_markup=await menu_for(session, user),
    )
