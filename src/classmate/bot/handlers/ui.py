"""Learner-facing navigation and class-scoped educational views."""

import asyncio
import logging
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from classmate.bot.keyboards import (
    cancel_kb,
    class_picker,
    education_menu,
    education_result_kb,
    main_menu,
)
from classmate.bot.states import JoinForm
from classmate.db.models import (
    AcademicYear,
    ClassGroup,
    ClassMembership,
    School,
    Subject,
    TimetableEntry,
    User,
)
from classmate.domain.dates import jalali_date, jalali_datetime
from classmate.services.permissions import get_class_with_school, user_can_access_class
from classmate.services.records import list_exams, list_homework, list_reports
from classmate.services.users import ensure_user

router = Router(name="ui")
logger = logging.getLogger(__name__)
WEEKDAYS = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]


async def render_panel(message: Message, text: str, markup) -> None:
    cleanup = await message.answer("\u2063", reply_markup=ReplyKeyboardRemove())
    try:
        await asyncio.sleep(0.1)
        await cleanup.delete()
    except Exception:
        logger.debug("keyboard cleanup failed", exc_info=True)
    await message.answer(text, reply_markup=markup)


async def delete_panel(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        logger.debug("panel deletion failed", exc_info=True)


async def get_user(session: AsyncSession, tg, private: bool = False) -> User:
    return await ensure_user(
        session,
        telegram_id=tg.id,
        username=tg.username,
        full_name=tg.full_name or tg.first_name or "User",
        private_chat_started=private,
    )


async def classes_for_user(session: AsyncSession, user: User) -> list[tuple[int, str, str]]:
    if not user.is_active:
        return []
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
            .distinct()
            .order_by(ClassGroup.name)
        )
    ).all()
    return [(cls.id, cls.name, school.name) for cls, school in rows]


async def menu_for(session: AsyncSession, user: User):
    return main_menu()


async def ask_class(message: Message, session: AsyncSession, action: str, user: User) -> None:
    classes = await classes_for_user(session, user)
    if not classes:
        await message.answer("هنوز عضو کلاسی نیستید. از «🔑 ورود با کد دعوت» استفاده کنید.")
        return
    if len(classes) == 1:
        await run_view(message, session, action, classes[0][0], user)
        return
    await render_panel(
        message, "🏫 کلاس موردنظر را انتخاب کنید:", class_picker(classes, action=action)
    )


async def _result(target: Message, text: str) -> None:
    await render_panel(target, text, education_result_kb())


async def run_view(
    target: Message, session: AsyncSession, action: str, class_id: int, user: User, **_
) -> None:
    if not await user_can_access_class(session, user, class_id):
        await _result(target, "دسترسی شما به این کلاس فعال نیست.")
        return
    ctx = await get_class_with_school(session, class_id)
    if ctx is None:
        await _result(target, "کلاس موردنظر پیدا نشد.")
        return
    cls, _, school = ctx
    today = datetime.now(ZoneInfo(school.timezone)).date()
    if action == "homework":
        rows = await list_homework(session, class_id)
        text = f"📚 <b>تکالیف — {escape(cls.name)}</b>" + (
            "\n"
            + "\n".join(
                f"• <b>{escape(s.name)}</b> — {escape(h.title)}\n  {escape(h.description)}\n  موعد: {jalali_date(h.due_on)}"
                for h, s in rows
            )
            if rows
            else "\nتکلیف تأییدشده‌ای نیست."
        )
    elif action == "exams":
        rows = await list_exams(session, class_id)
        text = f"📝 <b>امتحانات — {escape(cls.name)}</b>" + (
            "\n"
            + "\n".join(
                f"• <b>{escape(s.name)}</b> — {escape(e.title)}\n  {escape(e.scope or '—')}\n  {jalali_datetime(e.scheduled_at, school.timezone)}"
                for e, s in rows
            )
            if rows
            else "\nامتحان آینده‌ای نیست."
        )
    elif action == "today":
        rows = await list_reports(session, class_id, today)
        text = f"📋 <b>گزارش‌های امروز — {escape(cls.name)}</b>" + (
            "\n" + "\n".join(f"• {escape(r.title or 'گزارش')}\n{escape(r.body)}" for r in rows)
            if rows
            else "\nگزارشی برای امروز نیست."
        )
    elif action == "week":
        rows = (
            await session.execute(
                select(TimetableEntry, Subject)
                .join(Subject, TimetableEntry.subject_id == Subject.id)
                .where(TimetableEntry.class_id == class_id)
                .order_by(TimetableEntry.weekday, TimetableEntry.period)
            )
        ).all()
        text = f"📅 <b>برنامه — {escape(cls.name)}</b>" + (
            "\n"
            + "\n".join(
                f"{WEEKDAYS[e.weekday]} · زنگ {e.period}: {escape(s.name)}" for e, s in rows
            )
            if rows
            else "\nبرنامه تنظیم نشده."
        )
    else:
        return
    await _result(target, text)


@router.message(F.text.in_({"❌ انصراف"}))
async def btn_cancel(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await message.answer(
        "عملیات لغو شد.",
        reply_markup=await menu_for(
            session,
            await get_user(session, message.from_user, private=message.chat.type == "private"),
        ),
    )


@router.message(F.text.in_({"🏫 کلاس‌های من", "کلاس‌های من"}))
async def btn_classes(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user, private=True)
    classes = await classes_for_user(session, user)
    await message.answer(
        "هنوز عضو کلاسی نیستید."
        if not classes
        else "🏫 <b>کلاس‌های شما</b>\n"
        + "\n".join(f"• <b>{escape(n)}</b> — {escape(s)}" for _, n, s in classes)
    )


@router.message(F.text.in_({"🎓 بخش آموزشی", "بخش آموزشی"}))
async def btn_education(message: Message) -> None:
    if message.chat.type == "private":
        await render_panel(
            message, "🎓 <b>بخش آموزشی</b>\nگزینه موردنظر را انتخاب کنید:", education_menu()
        )


@router.message(F.text.in_({"🔑 ورود با کد دعوت", "عضویت با کد"}))
async def btn_join(message: Message, state: FSMContext) -> None:
    if message.chat.type == "private":
        await state.set_state(JoinForm.code)
        await message.answer("🔑 کد دعوت کلاس را ارسال کنید:", reply_markup=cancel_kb())


@router.message(F.text.in_({"❓ راهنما", "❓ راهنمای استفاده", "راهنما"}))
async def btn_help(message: Message, session: AsyncSession) -> None:
    await message.answer(
        "<b>راهنمای ClassMate 2</b>\n\n"
        "از «🔑 ورود با کد دعوت» عضو کلاس شوید.\n"
        "گزینه‌های «🎓 بخش آموزشی» فعلاً فقط جهت نمایش هستند و فعال نشده‌اند.\n\n"
        "🚧 ⌯ اگه کارت به مشکل خورده یا پیشنهادی برای ارتقا ربات داری میتونی از طریق آیدی زیر با ما در ارتباط باشی🤝🏻\n"
        "🗣️ @Mizanup",
        reply_markup=await menu_for(
            session,
            await get_user(session, message.from_user, private=message.chat.type == "private"),
        ),
    )


@router.callback_query(F.data == "ui:home")
async def cb_home(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if callback.message is None:
        return
    await state.clear()
    await delete_panel(callback.message)
    await callback.message.answer(
        "🏠 <b>منوی اصلی</b>",
        reply_markup=await menu_for(
            session, await get_user(session, callback.from_user, private=True)
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("edu:notice:"))
async def cb_education_notice(callback: CallbackQuery) -> None:
    await callback.answer(
        "⌯ این دکمه ها فعلا جهت نمایش هستند و فعالشون نکردیم❗",
        show_alert=True,
    )


@router.callback_query(F.data.startswith("menu:"))
async def cb_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None:
        return
    action = callback.data.split(":", 1)[1]
    if action == "education":
        await render_panel(callback.message, "🎓 <b>بخش آموزشی</b>", education_menu())
    elif action in {"homework", "exams", "week", "today"}:
        await ask_class(
            callback.message,
            session,
            action,
            await get_user(session, callback.from_user, private=True),
        )
    else:
        from classmate.bot.handlers.phase2 import my_attendance, my_grades, polls, submit_entry

        handler = {
            "submit": submit_entry,
            "grades": my_grades,
            "attendance": my_attendance,
            "polls": polls,
        }.get(action)
        if handler:
            await handler(callback.message, session)
    await callback.answer()


@router.callback_query(F.data.startswith("pick:"))
async def cb_pick(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None:
        return
    _, action, id_s = callback.data.split(":", 2)
    user = await get_user(session, callback.from_user, private=True)
    await delete_panel(callback.message)
    await run_view(callback.message, session, action, int(id_s), user)
    await callback.answer()
