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
        "به <b>ClassMate 2</b>، دستیار هوشمند مدیریت امور آموزشی، خوش آمدید.\n\n"
        "در اینجا می‌توانید کلاس‌ها، تکالیف، امتحانات، برنامه هفتگی و گزارش‌های آموزشی را در یک محیط منظم مدیریت کنید.\n\n"
        "<b>برای شروع</b>\n"
        "۱) با «🔑 ورود با کد دعوت» وارد کلاس شوید.\n"
        "۲) از «🏠 داشبورد من» فعالیت‌های نزدیک و اطلاعیه‌ها را بررسی کنید.\n"
        "۳) برای مشاهده همه امکانات، از دکمه‌های پایین صفحه استفاده کنید.\n\n"
        "<b>امکانات آموزشی جدید</b>\n"
        "📤 ارسال پاسخ تکلیف با متن، فایل یا تصویر\n"
        "📊 مشاهده نمرات و بازخوردهای آموزشی\n"
        "🟢 مشاهده سوابق حضور‌وغیاب\n"
        "🗳 شرکت در نظرسنجی‌ها و آزمون‌های کوتاه\n\n"
        "برای راهنمایی یا گزارش مشکل، «🆘 پشتیبانی» را انتخاب کنید."
    )
    if user.is_platform_admin:
        text += "\n\n🔑 دسترسی <b>مدیر سامانه</b> برای شما فعال است؛ برای ادامه، «🏛 مدیریت سامانه» را انتخاب کنید."

    await message.answer(text, reply_markup=await menu_for(session, user))
