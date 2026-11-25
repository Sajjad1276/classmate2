"""Student onboarding only: join an existing class and list memberships."""

from html import escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.bot.handlers.ui import get_user
from classmate.db.models import AcademicYear, ClassGroup, ClassMembership, School
from classmate.services.permissions import consume_class_member_invite_code

router = Router(name="onboarding")


@router.message(Command("join"))
async def join_class(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        await message.answer("برای عضویت، فرمان /join را در گفت‌وگوی خصوصی ربات اجرا کنید.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        await message.answer("قالب: /join کد_اختصاصی_عضویت")
        return

    code = parts[1].strip().upper()
    user = await get_user(session, message.from_user, private=True)
    if not user.is_active:
        await message.answer("حساب کاربری شما غیرفعال است.")
        return

    try:
        class_group = await consume_class_member_invite_code(
            session,
            code=code,
            user=user,
        )
    except (PermissionError, ValueError) as exc:
        await message.answer(str(exc))
        return

    await message.answer(
        f"با موفقیت به کلاس <b>{escape(class_group.name)}</b> ملحق شدید."
    )


@router.message(Command("classes"))
async def list_classes(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user, private=True)
    rows = (
        await session.execute(
            select(ClassGroup, School)
            .join(AcademicYear, ClassGroup.academic_year_id == AcademicYear.id)
            .join(School, AcademicYear.school_id == School.id)
            .join(
                ClassMembership,
                (ClassMembership.class_id == ClassGroup.id)
                & (ClassMembership.user_id == user.id)
                & ClassMembership.is_active.is_(True),
            )
            .where(ClassGroup.is_active.is_(True), School.is_active.is_(True))
            .order_by(ClassGroup.name)
        )
    ).all()
    if not rows:
        await message.answer("هنوز عضو کلاسی نیستید. از «🔑 ورود با کد دعوت» استفاده کنید.")
        return
    await message.answer(
        "🏫 <b>کلاس‌های شما</b>\n"
        + "\n".join(
            f"• <b>{escape(class_group.name)}</b> — {escape(school.name)}"
            for class_group, school in rows
        )
    )
