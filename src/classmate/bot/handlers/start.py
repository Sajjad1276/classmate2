from html import escape

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.bot.command_menu import setup_default_commands, sync_private_command_menu
from classmate.bot.handlers.ui import get_user, menu_for, role_flags
from classmate.core.config import get_settings

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    user = await get_user(session, message.from_user, private=message.chat.type == "private")

    if message.chat.type == "private":
        admin, manager, approver = await role_flags(session, user)
        await sync_private_command_menu(
            message.bot,
            message.chat.id,
            is_platform_admin=user.is_platform_admin,
            is_school_admin=admin,
            is_class_manager=manager,
            is_approver=approver,
        )

    name = escape(message.from_user.first_name or "")
    text = (
        f"سلام {name} گرامی 👋\n\n"
        "به <b>ClassMate 2</b> خوش آمدید.\n"
        "تمام امکانات از طریق <b>دکمه‌های پایین صفحه</b> و پنل‌های اینلاین در دسترس است؛ نیازی به حفظ کردن فرمان‌ها ندارید.\n\n"
        "از منو شروع کنید یا «🔑 ورود با کد دعوت» را انتخاب کنید.\n"
        "برای راهنمایی و پشتیبانی، گزینه «🆘 پشتیبانی» در منوی اصلی را باز کنید."
    )
    if user.is_platform_admin:
        text += "\n\n🔑 دسترسی <b>مدیر سامانه</b> برای شما فعال است؛ برای ادامه، «🏛 مدیریت سامانه» را انتخاب کنید."

    await message.answer(text, reply_markup=await menu_for(session, user))
