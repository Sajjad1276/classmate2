from datetime import datetime, timezone
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.core.config import get_settings
from classmate.core.enums import Role
from classmate.db.models import AcademicYear, ClassGroup, ClassMembership, School, SchoolMembership, User
from classmate.services.permissions import (
    activate_student_membership,
    code_expiry,
    connect_group_by_code,
    get_class_with_school,
    hash_code,
    is_school_admin,
    make_code,
    user_can_access_class,
)
from classmate.services.users import ensure_user

router = Router(name="onboarding")


async def _user(session: AsyncSession, tg_user, private: bool = False) -> User:
    settings = get_settings()
    user = await ensure_user(
        session,
        telegram_id=tg_user.id,
        username=tg_user.username,
        full_name=tg_user.full_name or tg_user.first_name or "User",
        private_chat_started=private,
    )
    if tg_user.id in settings.bootstrap_admin_id_set:
        user.is_platform_admin = True
    return user


@router.message(Command("school_new"))
async def school_new(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        await message.answer("لطفاً این فرمان را در گفت‌وگوی خصوصی ربات اجرا کنید.")
        return
    try:
        raw = message.text.split(maxsplit=1)[1]
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) not in {2, 3} or not parts[0] or not parts[1]:
            raise ValueError
        name, year_name = parts[0], parts[1]
        tz = (parts[2] if len(parts) == 3 else "Asia/Tehran") or "Asia/Tehran"
    except (IndexError, ValueError):
        await message.answer("قالب: /school_new نام مدرسه | ۱۴۰۵-۱۴۰۶ [| Asia/Tehran]")
        return

    user = await _user(session, message.from_user, private=True)
    if not user.is_platform_admin:
        await message.answer("فقط مدیر کل سامانه می‌تواند مدرسه بسازد.")
        return

    try:
        ZoneInfo(tz)
    except ZoneInfoNotFoundError:
        await message.answer("منطقه زمانی نامعتبر است. نمونه معتبر: Asia/Tehran")
        return

    school = School(name=name[:180], timezone=tz)
    session.add(school)
    await session.flush()
    year = AcademicYear(school_id=school.id, name=year_name[:40], is_current=True)
    session.add(year)
    session.add(
        SchoolMembership(school_id=school.id, user_id=user.id, role=Role.SCHOOL_ADMIN)
    )
    await session.flush()
    await message.answer(
        f"مدرسه <b>{escape(school.name)}</b> ساخته شد.\n"
        f"شناسه مدرسه: <code>{school.id}</code>\n"
        f"سال تحصیلی فعال: {escape(year.name)} (شناسه: <code>{year.id}</code>)"
    )


@router.message(Command("class_new"))
async def class_new(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        await message.answer("لطفاً این فرمان را در گفت‌وگوی خصوصی ربات اجرا کنید.")
        return
    try:
        raw = message.text.split(maxsplit=1)[1]
        school_id_s, grade, section, class_name = [p.strip() for p in raw.split("|")]
        school_id = int(school_id_s)
    except (IndexError, ValueError):
        await message.answer("قالب: /class_new شناسه_مدرسه | پایه | شعبه | نام کلاس")
        return

    user = await _user(session, message.from_user, private=True)
    if not await is_school_admin(session, user, school_id):
        await message.answer("شما دسترسی مدیریت این مدرسه را ندارید.")
        return

    year = await session.scalar(
        select(AcademicYear).where(
            AcademicYear.school_id == school_id, AcademicYear.is_current.is_(True)
        )
    )
    if year is None:
        await message.answer("سال تحصیلی فعالی برای این مدرسه موردنظر پیدا نشد.")
        return

    cls = ClassGroup(
        academic_year_id=year.id,
        name=class_name[:80],
        grade=grade[:32],
        section=section[:32] if section else None,
    )
    session.add(cls)
    await session.flush()
    session.add(
        ClassMembership(class_id=cls.id, user_id=user.id, role=Role.CLASS_MANAGER)
    )
    await message.answer(
        f"کلاس <b>{escape(cls.name)}</b> ساخته شد.\nشناسه کلاس: <code>{cls.id}</code>"
    )


@router.message(Command("class_code"))
async def class_code(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        await message.answer("لطفاً این عملیات را در گفت‌وگوی خصوصی ربات انجام دهید.")
        return
    try:
        class_id = int(message.text.split(maxsplit=1)[1].strip())
    except (IndexError, ValueError):
        await message.answer("قالب: /class_code شناسه_کلاس")
        return

    user = await _user(session, message.from_user, private=True)
    from classmate.services.permissions import is_class_manager

    if not await is_class_manager(session, user, class_id):
        await message.answer("تولید کد فقط برای مدیر کلاس مجاز است.")
        return

    cls = await session.get(ClassGroup, class_id)
    if cls is None:
        await message.answer("کلاس موردنظر پیدا نشد. لطفاً دوباره تلاش کنید.")
        return

    group_code = make_code(8)
    join_code = make_code(8)
    cls.group_link_code_hash = hash_code(group_code)
    cls.group_link_code_expires_at = code_expiry(30)
    cls.member_join_code_hash = hash_code(join_code)
    cls.member_join_code_expires_at = code_expiry(60 * 24 * 7)

    await message.answer(
        f"کدهای دسترسی کلاس <b>{escape(cls.name)}</b>:\n\n"
        f"کد اتصال گروه (اعتبار: ۳۰ دقیقه): <code>{group_code}</code>\n"
        f"کد دعوت دانش‌آموز (اعتبار: ۷ روز): <code>{join_code}</code>\n\n"
        f"در گروه: /connect {group_code}\n"
        f"در خصوصی: /join {join_code}"
    )


@router.message(Command("connect"))
async def connect_group(message: Message, session: AsyncSession) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("لطفاً این فرمان را داخل گروه کلاس اجرا کنید.")
        return
    try:
        code = message.text.split(maxsplit=1)[1].strip().upper()
    except IndexError:
        await message.answer("قالب: /connect کد_اتصال")
        return

    user = await _user(session, message.from_user)
    try:
        bot_user = await message.bot.me()
        bot_member = await message.bot.get_chat_member(message.chat.id, bot_user.id)
        requester_member = await message.bot.get_chat_member(
            message.chat.id, message.from_user.id
        )
    except Exception:
        await message.answer(
            "امکان بررسی مدیران گروه وجود ندارد. مطمئن شوید ربات در گروه است و دوباره تلاش کنید."
        )
        return

    try:
        cls = await connect_group_by_code(
            session,
            code=code,
            chat_id=message.chat.id,
            chat_title=message.chat.title,
            actor=user,
            bot_is_admin=bot_member.status in {"administrator", "creator"},
            actor_is_admin=requester_member.status in {"administrator", "creator"},
        )
    except (PermissionError, ValueError) as exc:
        await message.answer(str(exc))
        return

    await message.answer(f"گروه با موفقیت به کلاس <b>{escape(cls.name)}</b> متصل شد.")


@router.message(Command("join"))
async def join_class(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        await message.answer("برای عضویت، فرمان /join را در گفت‌وگوی خصوصی ربات اجرا کنید.")
        return
    try:
        code = message.text.split(maxsplit=1)[1].strip().upper()
    except IndexError:
        await message.answer("قالب: /join کد_عضویت")
        return

    user = await _user(session, message.from_user, private=True)
    code_h = hash_code(code)
    cls = await session.scalar(
        select(ClassGroup)
        .join(AcademicYear, ClassGroup.academic_year_id == AcademicYear.id)
        .join(School, AcademicYear.school_id == School.id)
        .where(
            ClassGroup.member_join_code_hash == code_h,
            ClassGroup.is_active.is_(True),
            School.is_active.is_(True),
        )
    )
    if cls is None:
        await message.answer("کد عضویت واردشده معتبر نیست.")
        return
    if (
        cls.member_join_code_expires_at is None
        or cls.member_join_code_expires_at.replace(tzinfo=cls.member_join_code_expires_at.tzinfo or timezone.utc)
        < datetime.now(timezone.utc)
    ):
        await message.answer("این کد منقضی شده است.")
        return

    await activate_student_membership(session, cls.id, user.id)
    await message.answer(f"با موفقیت به کلاس <b>{escape(cls.name)}</b> ملحق شدید.")


@router.message(Command("classes"))
async def list_classes(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        await message.answer("این فرمان را در گفت‌وگوی خصوصی ربات اجرا کنید: /classes")
        return
    user = await _user(session, message.from_user, private=True)

    from sqlalchemy import or_

    query = (
        select(ClassGroup)
        .join(AcademicYear, ClassGroup.academic_year_id == AcademicYear.id)
        .join(School, AcademicYear.school_id == School.id)
        .outerjoin(
            ClassMembership,
            (ClassMembership.class_id == ClassGroup.id)
            & (ClassMembership.user_id == user.id)
            & ClassMembership.is_active.is_(True),
        )
        .outerjoin(
            SchoolMembership,
            (SchoolMembership.school_id == AcademicYear.school_id)
            & (SchoolMembership.user_id == user.id)
            & (SchoolMembership.role == Role.SCHOOL_ADMIN)
            & SchoolMembership.is_active.is_(True),
        )
        .where(
            ClassGroup.is_active.is_(True),
            School.is_active.is_(True),
            or_(
                ClassMembership.id.is_not(None),
                SchoolMembership.id.is_not(None),
                user.is_platform_admin,
            ),
        )
        .distinct()
        .order_by(ClassGroup.name)
    )
    classes = list((await session.execute(query)).scalars().all())
    if not classes:
        await message.answer("هنوز عضو کلاسی نیستید. با فرمان /join کد_عضویت به کلاس ملحق شوید.")
        return

    lines = ["🏫 <b>کلاس‌های شما</b>"]
    for c in classes:
        ctx = await get_class_with_school(session, c.id)
        school_name = ctx[2].name if ctx else "؟"
        lines.append(f"• <b>{escape(c.name)}</b> — {escape(school_name)} | شناسه: <code>{c.id}</code>")
    await message.answer("\n".join(lines))
