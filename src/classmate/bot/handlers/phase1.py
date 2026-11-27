"""Phase-one product features exposed through the button-first UX."""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.bot.handlers.ui import (
    delete_panel,
    classes_for_user,
    get_user,
    menu_for,
    render_panel,
)
from classmate.bot.keyboards import cancel_kb, class_picker
from classmate.bot.states import JoinForm, SearchForm
from classmate.domain.dates import jalali_date, jalali_datetime, persian_weekday
from classmate.core.enums import RecordStatus
from classmate.db.models import ClassGroup, DailyReport, Exam, Homework, Subject
from classmate.services.permissions import get_class_with_school, user_can_access_class
from classmate.services.phase1 import (
    calendar_rows,
    dashboard_data,
    get_preferences,
    list_announcements,
    search_records,
)

router = Router(name="phase1")
logger = logging.getLogger(__name__)
WEEKDAYS = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]


async def _require_private_callback(callback: CallbackQuery) -> bool:
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer(
            "این عملیات فقط از گفت‌وگوی خصوصی ربات در دسترس است.",
            show_alert=True,
        )
        return False
    return True


async def _require_private_message(message: Message, state: FSMContext) -> bool:
    if message.chat.type != "private":
        await state.clear()
        await message.answer("این عملیات فقط در گفت‌وگوی خصوصی ربات انجام می‌شود.")
        return False
    return True


def dashboard_kb(back_callback: str | None = "ui:home") -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="📅 تقویم کلاس", callback_data="info:calendar:dashboard", style="primary"
            )
        ],
        [
            InlineKeyboardButton(text="📝 امتحانات", callback_data="info:exams:dashboard", style="primary"),
            InlineKeyboardButton(text="📚 تکالیف", callback_data="info:homework:dashboard", style="primary"),
        ],
        [
            InlineKeyboardButton(text="📋 گزارش‌های کلاسی", callback_data="info:reports:dashboard"),
            InlineKeyboardButton(text="📣 اطلاعیه‌ها", callback_data="info:announcements:dashboard"),
        ],
        [InlineKeyboardButton(text="🔔 تنظیم اعلان‌ها", callback_data="p1:preferences")],
    ]
    if back_callback is not None:
        rows.append(
            [InlineKeyboardButton(text="‹ بازگشت", callback_data=back_callback, style="danger")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)

def dashboard_empty_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔑 ورود با کد دعوت", callback_data="p1:join", style="success"
                )
            ],
            [InlineKeyboardButton(text="‹ بازگشت", callback_data="ui:home", style="danger")],
        ]
    )


def announcement_kb(back_callback: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    if back_callback is not None:
        rows.append([InlineKeyboardButton(text="‹ بازگشت", callback_data=back_callback, style="danger")])
    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


async def _render_dashboard(message: Message, session: AsyncSession, user) -> None:
    classes = await classes_for_user(session, user)
    if not classes:
        await render_panel(
            message,
            "🏠 <b>داشبورد من</b>\n\n"
            "هنوز عضو هیچ کلاسی نیستید. برای دیدن تکالیف، امتحانات و برنامه، ابتدا با کد دعوت وارد یک کلاس شوید.",
            dashboard_empty_kb(),
        )
        return
    try:
        data = await dashboard_data(session, [row[0] for row in classes])
        timezone_by_class = {}
        for class_id, _class_name, _school_name in classes:
            context = await get_class_with_school(session, class_id)
            if context is not None:
                timezone_by_class[class_id] = context[2].timezone
        first_timezone = timezone_by_class.get(classes[0][0], "Asia/Tehran")
        local_today = datetime.now(ZoneInfo(first_timezone)).date()
    except Exception:
        logger.exception("Could not load dashboard data", extra={"user_id": user.id})
        await render_panel(
            message,
            "🏠 <b>داشبورد من</b>\n\n"
            "اطلاعات داشبورد موقتاً در دسترس نیست. لطفاً دوباره به‌روزرسانی کنید یا از بخش آموزشی ادامه دهید.",
            dashboard_kb(),
        )
        return
    lines = [
        "🏠 <b>داشبورد من</b>",
        f"{jalali_date(local_today)}  ·  {len(classes)} کلاس فعال",
        "",
        f"🏫 {len(data['today_lessons'])} کلاس امروز  ·  📚 {len(data['homework'])} تکلیف  ·  📝 {len(data['exams'])} امتحان",
        f"📣 {len(data['announcements'])} اطلاعیه فعال",
        "فعالیت‌های مهم هفت روز آینده:",
    ]
    if data["today_lessons"]:
        lines.append("\n🏫 <b>برنامه امروز</b>")
        for entry, subject, cls in data["today_lessons"][:6]:
            time = entry.starts_at.strftime("%H:%M") if entry.starts_at else "زمان نامشخص"
            lines.append(f"• {time} — {escape(subject.name)} | {escape(cls.name)}")
    if data["homework"]:
        lines.append(f"\n📚 <b>تکالیف نزدیک</b> ({len(data['homework'])})")
        for hw, subject, cls in data["homework"][:5]:
            due = jalali_date(hw.due_on) if hw.due_on else "بدون موعد"
            lines.append(
                f"• {escape(subject.name)} — {escape(hw.title)} | {due} | {escape(cls.name)}"
            )
    else:
        lines.append("\n📚 تکلیف نزدیک به موعدی ندارید.")
    if data["exams"]:
        lines.append(f"\n📝 <b>امتحانات پیش‌رو</b> ({len(data['exams'])})")
        for exam, subject, cls in data["exams"][:5]:
            lines.append(
                f"• {escape(subject.name)} — {escape(exam.title)} | {jalali_datetime(exam.scheduled_at, timezone_by_class.get(cls.id, 'Asia/Tehran'))} | {escape(cls.name)}"
            )
    else:
        lines.append("\n📝 امتحان پیش‌رویی ثبت نشده است.")
    if data["announcements"]:
        lines.append(f"\n📣 <b>آخرین اطلاعیه‌ها</b> ({len(data['announcements'])})")
        for item, cls in data["announcements"][:3]:
            lines.append(f"• {escape(item.title)} — {escape(cls.name)}")
    else:
        lines.append("\n📣 اطلاعیه جدیدی برای کلاس‌های شما وجود ندارد.")
    await render_panel(message, "\n".join(lines), dashboard_kb())


@router.message(F.text.in_({"📊 وضعیت امروز", "🏠 داشبورد من", "داشبورد من"}))
async def dashboard(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    await _render_dashboard(message, session, user)


@router.callback_query(F.data == "p1:dashboard")
async def dashboard_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _require_private_callback(callback):
        return
    user = await get_user(
        session,
        callback.from_user,
        private=bool(callback.message and callback.message.chat.type == "private"),
    )
    await delete_panel(callback.message)
    await _render_dashboard(callback.message, session, user)
    await callback.answer("داشبورد به‌روزرسانی شد.")


@router.callback_query(F.data == "dashboard:today")
async def today_dashboard_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _require_private_callback(callback):
        return
    user = await get_user(
        session,
        callback.from_user,
        private=True,
    )
    await delete_panel(callback.message)
    await _render_dashboard(callback.message, session, user)
    await callback.answer()


@router.callback_query(F.data == "p1:calendar")
async def calendar_entry(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _require_private_callback(callback):
        return
    user = await get_user(
        session,
        callback.from_user,
        private=bool(callback.message and callback.message.chat.type == "private"),
    )
    classes = await classes_for_user(session, user)
    await delete_panel(callback.message)
    if not classes:
        await render_panel(
            callback.message,
            "هنوز عضو کلاسی نیستید. برای مشاهده تقویم، ابتدا با کد دعوت وارد یک کلاس شوید.",
            dashboard_empty_kb(),
        )
    elif len(classes) == 1:
        await _render_calendar(
            callback.message,
            session,
            classes[0][0],
            classes[0][1],
            back_callback="p1:dashboard",
        )
    else:
        await render_panel(
            callback.message,
            "🏫 کلاس موردنظر برای مشاهده تقویم را انتخاب کنید:",
            class_picker(classes, action="calendar", back_callback="p1:dashboard"),
        )
    await callback.answer()


@router.callback_query(F.data == "p1:join")
async def dashboard_join(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_private_callback(callback):
        return
    await delete_panel(callback.message)
    await state.set_state(JoinForm.code)
    await callback.message.answer("🔑 کد دعوت کلاس را ارسال کنید:", reply_markup=cancel_kb())
    await callback.answer()


@router.callback_query(F.data == "p1:preferences")
async def preferences_entry(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _require_private_callback(callback):
        return
    user = await get_user(
        session,
        callback.from_user,
        private=bool(callback.message and callback.message.chat.type == "private"),
    )
    classes = await classes_for_user(session, user)
    await delete_panel(callback.message)
    if not classes:
        await render_panel(
            callback.message,
            "برای تنظیم اعلان‌ها ابتدا عضو یک کلاس شوید.",
            dashboard_empty_kb(),
        )
    elif len(classes) == 1:
        await _render_preferences(callback.message, session, user, classes[0][0], classes[0][1], back_callback="p1:dashboard")
    else:
        await render_panel(
            callback.message,
            "🏫 کلاس موردنظر برای تنظیم اعلان‌ها را انتخاب کنید:",
            class_picker(classes, action="prefs", back_callback="p1:dashboard"),
        )
    await callback.answer()


async def _render_calendar(
    message: Message,
    session: AsyncSession,
    class_id: int,
    class_name: str,
    *,
    back_callback: str | None = None,
) -> None:
    context = await get_class_with_school(session, class_id)
    if context is None:
        await message.answer("کلاس موردنظر پیدا نشد.")
        return
    timezone_name = context[2].timezone
    local_today = datetime.now(ZoneInfo(timezone_name)).date()
    timetable, exams, homeworks = await calendar_rows(
        session, class_id, local_today, timezone_name=timezone_name
    )
    lines = [f"📅 <b>تقویم آموزشی — {escape(class_name)}</b>", "", "رویدادهای هفت روز آینده:"]
    if exams:
        lines.append("\n📝 <b>امتحانات</b>")
        for exam, subject in exams:
            lines.append(
                f"• {jalali_datetime(exam.scheduled_at, timezone_name)} — {escape(subject.name)}: {escape(exam.title)}"
            )
    if homeworks:
        lines.append("\n📚 <b>موعد تکالیف</b>")
        for hw, subject in homeworks:
            lines.append(f"• {jalali_date(hw.due_on)} — {escape(subject.name)}: {escape(hw.title)}")
    today_weekday = persian_weekday(local_today)
    todays = [(entry, subject) for entry, subject in timetable if entry.weekday == today_weekday]
    if todays:
        lines.append("\n🏫 <b>کلاس‌های امروز</b>")
        for entry, subject in todays:
            time = entry.starts_at.strftime("%H:%M") if entry.starts_at else "زمان نامشخص"
            lines.append(f"• زنگ {entry.period} — {escape(subject.name)} ({time})")
    if not exams and not homeworks and not todays:
        lines.append("\nبرای این بازه رویداد آموزشی ثبت نشده است.")
    await render_panel(message, "\n".join(lines), dashboard_kb(back_callback))


@router.message(F.text.in_({"🔎 جست‌وجو", "جست‌وجو"}))
async def search_entry(message: Message, state: FSMContext) -> None:
    if message.chat.type != "private":
        return
    await state.set_state(SearchForm.query)
    await message.answer(
        "🔎 عبارت جست‌وجو را وارد کنید؛ مانند نام درس، تکلیف یا امتحان:", reply_markup=cancel_kb()
    )


@router.message(SearchForm.query, F.text)
async def search_submit(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not await _require_private_message(message, state):
        return
    if message.text == "❌ انصراف":
        await state.clear()
        user = await get_user(session, message.from_user, private=message.chat.type == "private")
        await message.answer(
            "جست‌وجو لغو شد.",
            reply_markup=await menu_for(session, user),
        )
        return
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    classes = await classes_for_user(session, user)
    rows = await search_records(session, [row[0] for row in classes], message.text)
    await state.clear()
    if not rows:
        await message.answer(
            "نتیجه‌ای برای جست‌وجوی شما پیدا نشد.", reply_markup=await menu_for(session, user)
        )
        return
    lines = [f"🔎 <b>نتایج جست‌وجو برای «{escape(message.text.strip())}»</b>"]
    for kind, row in rows[:20]:
        if kind == "homework":
            item, subject, cls = row
            lines.append(f"\n📚 {escape(subject.name)} — {escape(item.title)} | {escape(cls.name)}")
        elif kind == "exam":
            item, subject, cls = row
            lines.append(f"\n📝 {escape(subject.name)} — {escape(item.title)} | {escape(cls.name)}")
        else:
            item, cls = row
            lines.append(f"\n📋 {escape(item.title or 'گزارش روزانه')} | {escape(cls.name)}")
    await message.answer("\n".join(lines), reply_markup=await menu_for(session, user))


@router.message(F.text.in_({"🔔 اعلان‌ها", "🔔 تنظیم اعلان‌ها", "اعلان‌ها", "تنظیم اعلان‌ها"}))
async def notification_settings(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    classes = await classes_for_user(session, user)
    if not classes:
        await message.answer("برای تنظیم اعلان‌ها ابتدا عضو یک کلاس شوید.")
        return
    if len(classes) > 1:
        await render_panel(
            message,
            "🏫 کلاس موردنظر را انتخاب کنید:",
            class_picker(classes, action="prefs", back_callback="ui:home"),
        )
        return
    await _render_preferences(message, session, user, classes[0][0], classes[0][1])


async def _render_preferences(
    message: Message,
    session: AsyncSession,
    user,
    class_id: int,
    class_name: str,
    *,
    back_callback: str = "ui:home",
) -> None:
    pref = await get_preferences(session, user.id, class_id)
    back_token = {"ui:home": "main_menu", "p1:dashboard": "dashboard"}.get(back_callback, "main_menu")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"📚 تکالیف: {'فعال' if pref.homework_enabled else 'خاموش'}",
                    callback_data=f"p1:toggle:hw:{class_id}:{back_token}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"📝 امتحانات: {'فعال' if pref.exams_enabled else 'خاموش'}",
                    callback_data=f"p1:toggle:exam:{class_id}:{back_token}",
                )
            ],
            [InlineKeyboardButton(text="‹ بازگشت", callback_data=back_callback, style="danger")],
        ]
    )
    await render_panel(
        message,
        f"🔔 <b>تنظیم اعلان‌ها — {escape(class_name)}</b>\n\nاعلان‌های موردنظر را فعال یا خاموش کنید.",
        kb,
    )


@router.callback_query(F.data.startswith("p1:toggle:"))
async def toggle_preference(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _require_private_callback(callback):
        return
    _, _, kind, class_id_s, *back_parts = callback.data.split(":")
    back_token = back_parts[0] if back_parts else "dashboard"
    back_callback = {"main_menu": "ui:home", "dashboard": "p1:dashboard"}.get(back_token, "p1:dashboard")
    user = await get_user(
        session,
        callback.from_user,
        private=bool(callback.message and callback.message.chat.type == "private"),
    )
    class_id = int(class_id_s)
    if not await user_can_access_class(session, user, class_id):
        await callback.answer("دسترسی شما به این کلاس فعال نیست.", show_alert=True)
        return
    pref = await get_preferences(session, user.id, class_id)
    if kind == "hw":
        pref.homework_enabled = not pref.homework_enabled
    else:
        pref.exams_enabled = not pref.exams_enabled
    await session.commit()
    await callback.message.edit_reply_markup(
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"📚 تکالیف: {'فعال' if pref.homework_enabled else 'خاموش'}",
                        callback_data=f"p1:toggle:hw:{class_id}:{back_token}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=f"📝 امتحانات: {'فعال' if pref.exams_enabled else 'خاموش'}",
                        callback_data=f"p1:toggle:exam:{class_id}:{back_token}",
                    )
                ],
                [InlineKeyboardButton(text="‹ بازگشت", callback_data=back_callback, style="danger")],
            ]
        )
    )
    await callback.answer("تنظیم اعلان‌ها به‌روزرسانی شد.")


@router.message(F.text.in_({"📣 اطلاعیه‌ها", "اطلاعیه‌ها"}))
async def announcements(
    message: Message,
    session: AsyncSession,
    *,
    back_callback: str | None = "ui:home",
) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    classes = await classes_for_user(session, user)
    rows = await list_announcements(session, [row[0] for row in classes])
    if not rows:
        text = "📣 <b>اطلاعیه‌ها</b>\n\nاطلاعیه فعالی برای کلاس‌های شما وجود ندارد."
    else:
        parts = ["📣 <b>آخرین اطلاعیه‌ها</b>"]
        for item, cls in rows:
            parts.append(f"\n<b>{escape(item.title)}</b> — {escape(cls.name)}\n{escape(item.body)}")
        text = "\n".join(parts)
    await render_panel(message, text, announcement_kb(back_callback))
