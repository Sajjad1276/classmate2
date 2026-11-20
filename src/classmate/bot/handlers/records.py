"""Submit homework / exam / report via simple command forms."""

from datetime import datetime
from zoneinfo import ZoneInfo
from html import escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.core.config import get_settings
from classmate.domain.dates import parse_school_date, parse_school_datetime
from classmate.services.permissions import get_class_with_school, user_can_access_class
from classmate.services.outbox import enqueue_telegram_message
from classmate.services.records import (
    add_subject,
    create_daily_report,
    create_exam,
    create_homework,
    get_group_context,
    get_subject,
)
from classmate.services.users import ensure_user

router = Router(name="records")


async def _user(session, tg, private=False):
    settings = get_settings()
    user = await ensure_user(
        session,
        telegram_id=tg.id,
        username=tg.username,
        full_name=tg.full_name or "User",
        private_chat_started=private,
    )
    if tg.id in settings.bootstrap_admin_id_set:
        user.is_platform_admin = True
    return user


async def _ctx(message, session, class_id: int | None = None):
    user = await _user(session, message.from_user, private=message.chat.type == "private")
    if not user.is_active:
        return None, user
    if message.chat.type in {"group", "supergroup"}:
        return await get_group_context(session, message.chat.id), user
    if class_id is None:
        return None, user
    ctx = await get_class_with_school(session, class_id)
    if ctx is None or not await user_can_access_class(session, user, class_id):
        return None, user
    return ctx, user


@router.message(Command("subject_add"))
async def subject_add(message: Message, session: AsyncSession) -> None:
    try:
        name = message.text.split(maxsplit=1)[1].strip()
    except IndexError:
        await message.answer("قالب: /subject_add نام درس")
        return
    ctx, user = await _ctx(message, session)
    if ctx is None:
        await message.answer("لطفاً این فرمان را در گروه کلاس اجرا کنید.")
        return
    class_group, _, school = ctx
    from classmate.services.permissions import is_class_manager

    if not await is_class_manager(session, user, class_group.id):
        await message.answer("فقط مدیر کلاس می‌تواند درس اضافه کند.")
        return
    subj = await add_subject(session, class_group.id, school.id, name)
    await message.answer(f"درس <b>{escape(subj.name)}</b> اضافه شد.")


@router.message(Command("new_homework"))
async def new_homework(message: Message, session: AsyncSession) -> None:
    """/new_homework درس | عنوان | توضیحات | موعد(اختیاری YYYY/MM/DD)"""
    try:
        raw = message.text.split(maxsplit=1)[1]
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) < 3:
            raise ValueError
        subject_name, title, description = parts[0], parts[1], parts[2]
        due_raw = parts[3] if len(parts) > 3 else None
    except (IndexError, ValueError):
        await message.answer(
            "قالب:\n/new_homework درس | عنوان | توضیحات | موعد(اختیاری)\n"
            "مثال: /new_homework ریاضی | تمرین صفحه ۲۰ | مسائل ۱ تا ۵ | ۱۴۰۵/۰۸/۰۱"
        )
        return

    ctx, user = await _ctx(message, session)
    if ctx is None:
        await message.answer("این عملیات را در گروه کلاس اجرا کنید؛ پشتیبانی از اجرای خصوصی با شناسه کلاس به‌زودی اضافه می‌شود.")
        return
    class_group, _, school = ctx
    subject = await get_subject(session, class_group.id, school.id, subject_name)
    if subject is None:
        await message.answer(f"درس «{escape(subject_name)}» در این کلاس ثبت نشده. اول /subject_add")
        return

    due_on = None
    if due_raw:
        try:
            due_on = parse_school_date(due_raw)
        except ValueError as e:
            await message.answer(str(e))
            return

    record = await create_homework(
        session, class_group.id, subject, title, description, due_on, user.id
    )
    body = (
        f"🕓 <b>تکلیف جدید برای بررسی</b>\n"
        f"درس: {escape(subject.name)}\n"
        f"عنوان: {escape(record.title)}\n"
        f"ثبت‌کننده: {escape(user.full_name)}"
    )
    if class_group.group_chat_id:
        await enqueue_telegram_message(
            session,
            target_type="group",
            target_chat_id=class_group.group_chat_id,
            class_id=class_group.id,
            text=body,
            action_type="homework",
            action_id=record.id,
            entity_type="homework",
            entity_id=record.id,
            expected_record_status="PENDING",
            idempotency_key=f"approval:homework:{record.id}",
        )
    await session.commit()
    await message.answer(f"تکلیف ثبت شد و در انتظار بررسی و تأیید است (#{record.id}).")


@router.message(Command("new_exam"))
async def new_exam(message: Message, session: AsyncSession) -> None:
    """/new_exam درس | عنوان | محدوده | زمان ۱۴۰۵/۰۸/۰۱ ۱۸:۳۰"""
    try:
        raw = message.text.split(maxsplit=1)[1]
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) < 4:
            raise ValueError
        subject_name, title, scope, when_raw = parts[0], parts[1], parts[2], parts[3]
    except (IndexError, ValueError):
        await message.answer(
            "قالب:\n/new_exam درس | عنوان | محدوده | زمان\n"
            "مثال: /new_exam فیزیک | میان‌ترم | فصل ۱ و ۲ | ۱۴۰۵/۰۸/۱۰ ۱۰:۰۰"
        )
        return

    ctx, user = await _ctx(message, session)
    if ctx is None:
        await message.answer("لطفاً این عملیات را در گروه کلاس انجام دهید.")
        return
    class_group, _, school = ctx
    subject = await get_subject(session, class_group.id, school.id, subject_name)
    if subject is None:
        await message.answer("درس موردنظر پیدا نشد. اول /subject_add")
        return
    try:
        scheduled = parse_school_datetime(when_raw, school.timezone)
    except ValueError as e:
        await message.answer(str(e))
        return

    record = await create_exam(
        session, class_group.id, subject, title, scope, "quiz", scheduled, user.id
    )
    body = (
        f"🕓 <b>امتحان جدید برای بررسی</b>\n"
        f"درس: {escape(subject.name)}\n"
        f"عنوان: {escape(record.title)}\n"
        f"ثبت‌کننده: {escape(user.full_name)}"
    )
    if class_group.group_chat_id:
        await enqueue_telegram_message(
            session,
            target_type="group",
            target_chat_id=class_group.group_chat_id,
            class_id=class_group.id,
            text=body,
            action_type="exam",
            action_id=record.id,
            entity_type="exam",
            entity_id=record.id,
            expected_record_status="PENDING",
            idempotency_key=f"approval:exam:{record.id}",
        )
    await session.commit()
    await message.answer(f"امتحان ثبت شد و در انتظار بررسی و تأیید است (#{record.id}).")


@router.message(Command("new_report"))
async def new_report(message: Message, session: AsyncSession) -> None:
    """/new_report متن گزارش [| عنوان]"""
    try:
        raw = message.text.split(maxsplit=1)[1].strip()
        if "|" in raw:
            body, title = [p.strip() for p in raw.split("|", 1)]
        else:
            body, title = raw, "گزارش روزانه"
    except IndexError:
        await message.answer("قالب: /new_report متن گزارش [| عنوان]")
        return

    ctx, user = await _ctx(message, session)
    if ctx is None:
        await message.answer("لطفاً این عملیات را در گروه کلاس انجام دهید.")
        return
    class_group, _, school = ctx
    local_today = datetime.now(ZoneInfo(school.timezone)).date()
    record = await create_daily_report(
        session, class_group.id, local_today, body, user.id, title=title
    )
    text = (
        f"🕓 <b>گزارش روزانه برای بررسی</b>\n"
        f"عنوان: {escape(record.title or '')}\n"
        f"ثبت‌کننده: {escape(user.full_name)}"
    )
    if class_group.group_chat_id:
        await enqueue_telegram_message(
            session,
            target_type="group",
            target_chat_id=class_group.group_chat_id,
            class_id=class_group.id,
            text=text,
            action_type="report",
            action_id=record.id,
            entity_type="report",
            entity_id=record.id,
            expected_record_status="PENDING",
            idempotency_key=f"approval:report:{record.id}",
        )
    await session.commit()
    await message.answer(f"گزارش ثبت شد و در انتظار بررسی و تأیید است (#{record.id}).")


@router.message(Command("timetable_set"))
async def timetable_set(message: Message, session: AsyncSession) -> None:
    """/timetable_set روز | زنگ | درس | شروع-پایان"""
    from classmate.db.models import TimetableEntry
    from classmate.domain.dates import persian_weekday
    from classmate.services.permissions import is_class_manager
    from datetime import time as dtime

    WEEK = {"شنبه": 0, "یکشنبه": 1, "دوشنبه": 2, "سه‌شنبه": 3, "سه شنبه": 3, "چهارشنبه": 4, "پنجشنبه": 5, "جمعه": 6}
    try:
        raw = message.text.split(maxsplit=1)[1]
        day_s, period_s, subject_name, time_range = [p.strip() for p in raw.split("|")]
        period = int(period_s)
        weekday = WEEK.get(day_s)
        if weekday is None:
            raise ValueError("روز نامعتبر")
        start_s, end_s = time_range.split("-")
        sh, sm = map(int, start_s.split(":"))
        eh, em = map(int, end_s.split(":"))
        starts, ends = dtime(sh, sm), dtime(eh, em)
    except (ValueError, IndexError, TypeError):
        await message.answer(
            "قالب: /timetable_set شنبه | ۱ | ریاضی | 08:00-08:45"
        )
        return

    if not 1 <= period <= 20:
        await message.answer("شماره زنگ باید بین ۱ تا ۲۰ باشد.")
        return
    if ends <= starts:
        await message.answer("ساعت پایان باید بعد از ساعت شروع باشد.")
        return

    ctx, user = await _ctx(message, session)
    if ctx is None:
        await message.answer("لطفاً این عملیات را در گروه کلاس انجام دهید.")
        return
    class_group, _, school = ctx
    if not await is_class_manager(session, user, class_group.id):
        await message.answer("این عملیات فقط برای مدیر کلاس مجاز است.")
        return
    subject = await get_subject(session, class_group.id, school.id, subject_name)
    if subject is None:
        subject = await add_subject(session, class_group.id, school.id, subject_name)

    from sqlalchemy import select

    existing = await session.scalar(
        select(TimetableEntry).where(
            TimetableEntry.class_id == class_group.id,
            TimetableEntry.weekday == weekday,
            TimetableEntry.period == period,
        )
    )
    if existing:
        existing.subject_id = subject.id
        existing.starts_at = starts
        existing.ends_at = ends
    else:
        session.add(
            TimetableEntry(
                class_id=class_group.id,
                subject_id=subject.id,
                weekday=weekday,
                period=period,
                starts_at=starts,
                ends_at=ends,
            )
        )
    await message.answer(f"زنگ {period} روز {day_s} → {escape(subject.name)} تنظیم شد.")
