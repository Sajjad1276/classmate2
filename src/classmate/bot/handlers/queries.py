from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.domain.dates import jalali_date, jalali_datetime
from classmate.services.permissions import get_class_with_school, user_can_access_class
from classmate.services.records import get_group_context, list_exams, list_homework, list_reports
from classmate.services.users import ensure_user
from classmate.core.config import get_settings
from classmate.db.models import TimetableEntry, Subject
from sqlalchemy import or_, select

router = Router(name="queries")

WEEKDAYS = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]


async def _resolve_class(message: Message, session: AsyncSession, arg: str | None):
    user = await ensure_user(
        session,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name or "User",
        private_chat_started=message.chat.type == "private",
    )
    settings = get_settings()
    if message.from_user.id in settings.bootstrap_admin_id_set:
        user.is_platform_admin = True

    if not user.is_active:
        return None, user
    if message.chat.type in {"group", "supergroup"}:
        return await get_group_context(session, message.chat.id), user

    if not arg:
        return None, user
    try:
        class_id = int(arg.split()[0])
    except ValueError:
        return None, user
    ctx = await get_class_with_school(session, class_id)
    if ctx is None or not await user_can_access_class(session, user, class_id):
        return None, user
    return ctx, user


@router.message(Command("homework"))
async def cmd_homework(message: Message, session: AsyncSession) -> None:
    arg = message.text.split(maxsplit=1)[1] if message.text and " " in message.text else None
    ctx, _ = await _resolve_class(message, session, arg)
    if ctx is None:
        await message.answer("کلاس موردنظر پیدا نشد. لطفاً دوباره تلاش کنید. در گروه ارسال کنید یا از /homework شناسه_کلاس استفاده کنید")
        return
    class_group, _, _ = ctx
    rows = await list_homework(session, class_group.id)
    if not rows:
        await message.answer("هنوز تکلیف تأییدشده‌ای برای این کلاس ثبت نشده است.")
        return
    lines = ["📚 <b>تکالیف تأییدشده</b>"]
    for hw, subj in rows:
        due = jalali_date(hw.due_on)
        lines.append(
            f"\n• <b>{escape(subj.name)}</b> — {escape(hw.title)}\n"
            f"  {escape(hw.description)}\n  موعد: {due}"
        )
    await message.answer("\n".join(lines))


@router.message(Command("exams"))
async def cmd_exams(message: Message, session: AsyncSession) -> None:
    arg = message.text.split(maxsplit=1)[1] if message.text and " " in message.text else None
    ctx, _ = await _resolve_class(message, session, arg)
    if ctx is None:
        await message.answer("کلاس موردنظر پیدا نشد. لطفاً دوباره تلاش کنید.")
        return
    class_group, _, school = ctx
    rows = await list_exams(session, class_group.id)
    if not rows:
        await message.answer("امتحان پیش‌رویی برای این کلاس ثبت نشده است.")
        return
    lines = ["📝 <b>امتحانات پیش‌رو</b>"]
    for exam, subj in rows:
        lines.append(
            f"\n• <b>{escape(subj.name)}</b> — {escape(exam.title)}\n"
            f"  محدوده: {escape(exam.scope or '—')}\n"
            f"  زمان: {jalali_datetime(exam.scheduled_at, school.timezone)}"
        )
    await message.answer("\n".join(lines))


@router.message(Command("today"))
async def cmd_today(message: Message, session: AsyncSession) -> None:
    arg = message.text.split(maxsplit=1)[1] if message.text and " " in message.text else None
    ctx, _ = await _resolve_class(message, session, arg)
    if ctx is None:
        await message.answer("کلاس موردنظر پیدا نشد. لطفاً دوباره تلاش کنید.")
        return
    class_group, _, school = ctx
    today = datetime.now(ZoneInfo(school.timezone)).date()
    reports = await list_reports(session, class_group.id, today)
    if not reports:
        await message.answer(f"گزارش تأییدشده‌ای برای امروز ({jalali_date(today)}) نیست.")
        return
    lines = [f"📋 <b>گزارش‌های امروز</b> ({jalali_date(today)})"]
    for r in reports:
        lines.append(f"\n• {escape(r.title or 'گزارش')}\n{escape(r.body)}")
    await message.answer("\n".join(lines))


@router.message(Command("week"))
async def cmd_week(message: Message, session: AsyncSession) -> None:
    arg = message.text.split(maxsplit=1)[1] if message.text and " " in message.text else None
    ctx, _ = await _resolve_class(message, session, arg)
    if ctx is None:
        await message.answer("کلاس موردنظر پیدا نشد. لطفاً دوباره تلاش کنید.")
        return
    class_group, _, school = ctx
    local_today = datetime.now(ZoneInfo(school.timezone)).date()
    entries = (
        await session.execute(
            select(TimetableEntry, Subject)
            .join(Subject, TimetableEntry.subject_id == Subject.id)
            .where(
                TimetableEntry.class_id == class_group.id,
                or_(
                    TimetableEntry.valid_from.is_(None),
                    TimetableEntry.valid_from <= local_today,
                ),
                or_(
                    TimetableEntry.valid_until.is_(None),
                    TimetableEntry.valid_until >= local_today,
                ),
            )
            .order_by(TimetableEntry.weekday, TimetableEntry.period)
        )
    ).all()
    if not entries:
        await message.answer("برنامه هفتگی هنوز تنظیم نشده است. مدیر کلاس می‌تواند آن را از بخش «مدیریت کلاس» تنظیم کند.")
        return
    lines = ["📅 <b>برنامه هفتگی</b>"]
    current_day = -1
    for entry, subj in entries:
        if entry.weekday != current_day:
            current_day = entry.weekday
            lines.append(f"\n<b>{WEEKDAYS[entry.weekday]}</b>")
        time_s = ""
        if entry.starts_at:
            time_s = f" {entry.starts_at.strftime('%H:%M')}"
            if entry.ends_at:
                time_s += f"-{entry.ends_at.strftime('%H:%M')}"
        lines.append(f"  {entry.period}. {escape(subj.name)}{time_s}")
    await message.answer("\n".join(lines))
