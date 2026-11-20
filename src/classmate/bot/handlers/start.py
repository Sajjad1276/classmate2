from html import escape

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.bot.command_menu import sync_private_command_menu
from classmate.bot.keyboards import main_menu
from classmate.core.config import get_settings
from classmate.core.enums import Role
from classmate.db.models import ClassMembership, SchoolMembership
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

    is_school_admin = user.is_platform_admin
    is_class_manager = user.is_platform_admin
    is_approver = user.is_platform_admin

    if message.chat.type == "private" and not user.is_platform_admin:
        is_school_admin = (
            await session.scalar(
                select(SchoolMembership.id).where(
                    SchoolMembership.user_id == user.id,
                    SchoolMembership.role == Role.SCHOOL_ADMIN,
                    SchoolMembership.is_active.is_(True),
                )
            )
        ) is not None
        roles = set(
            (
                await session.scalars(
                    select(ClassMembership.role).where(
                        ClassMembership.user_id == user.id,
                        ClassMembership.is_active.is_(True),
                    )
                )
            ).all()
        )
        is_class_manager = Role.CLASS_MANAGER in roles or is_school_admin
        is_approver = is_school_admin or is_class_manager or Role.TRUSTED_APPROVER in roles

    if message.chat.type == "private":
        await sync_private_command_menu(
            message.bot,
            message.chat.id,
            is_platform_admin=user.is_platform_admin,
            is_school_admin=is_school_admin,
            is_class_manager=is_class_manager,
            is_approver=is_approver,
        )

    name = escape(message.from_user.first_name or "")
    text = (
        f"سلام {name} 👋\n\n"
        "به <b>ClassMate 2</b> خوش آمدی.\n"
        "تکالیف، امتحانات، گزارش‌ها و برنامه کلاس — منظم و با تأیید افراد مورد اعتماد.\n\n"
        "از دکمه‌های پایین یا منوی دستورات (/) استفاده کن."
    )
    if user.is_platform_admin:
        text += "\n\n🔑 دسترسی <b>مدیر کل</b> برای تو فعال است."

    await message.answer(
        text,
        reply_markup=main_menu(is_approver=is_approver, is_manager=is_class_manager),
    )
