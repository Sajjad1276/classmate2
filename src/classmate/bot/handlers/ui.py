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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from classmate.bot.keyboards import (
    cancel_kb,
    class_picker,
    information_menu,
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
from classmate.services.permissions import get_class_with_school, is_class_manager, user_can_access_class
from classmate.services.records import list_exams, list_homework, list_reports
from classmate.services.users import ensure_user

router = Router(name="ui")
logger = logging.getLogger(__name__)
LINE_BREAK = chr(10)
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
    return main_menu(is_owner=bool(user.is_platform_admin))


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
    if not classes:
        await message.answer("هنوز عضو کلاسی نیستید.")
        return

    rows = []
    for class_id, class_name, school_name in classes:
        if await is_class_manager(session, user, class_id):
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"🔗 اتصال گروه · {class_name[:35]}",
                        callback_data=f"group:manage:{class_id}",
                    )
                ]
            )
    await message.answer(
        "🏫 <b>کلاس‌های شما</b>\n"
        + "\n".join(
            f"• <b>{escape(name)}</b> — {escape(school)}"
            for _, name, school in classes
        )
        + (
            "\n\nبرای کلاس‌هایی که مدیر آن هستید، مدیریت اتصال گروه از دکمه‌های زیر در دسترس است."
            if rows
            else ""
        ),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None,
    )


@router.message(F.text.in_({"🏫 کلاس‌ها", "مدیریت کلاس‌ها"}))
async def owner_classes_message(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user, private=True)
    if not user.is_platform_admin:
        await message.answer("این بخش فقط برای مالک سامانه فعال است.")
        return
    await _render_owner_classes(message, session, page=0)


async def _render_owner_classes(message: Message, session: AsyncSession, *, page: int) -> None:
    page_size = 8
    total = await session.scalar(
        select(func.count(ClassGroup.id))
        .join(AcademicYear, ClassGroup.academic_year_id == AcademicYear.id)
        .join(School, AcademicYear.school_id == School.id)
        .where(ClassGroup.is_active.is_(True), School.is_active.is_(True))
    ) or 0
    rows = (await session.execute(
        select(ClassGroup, AcademicYear, School)
        .join(AcademicYear, ClassGroup.academic_year_id == AcademicYear.id)
        .join(School, AcademicYear.school_id == School.id)
        .where(ClassGroup.is_active.is_(True), School.is_active.is_(True))
        .order_by(School.name, AcademicYear.name, ClassGroup.name)
        .offset(page * page_size).limit(page_size)
    )).all()
    buttons = [
        [InlineKeyboardButton(
            text=f"🏫 {cls.name[:28]} · {school.name[:22]}",
            callback_data=f"owner:class:{cls.id}",
        )]
        for cls, year, school in rows
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="‹ قبلی", callback_data=f"owner:classes:{page-1}"))
    if (page + 1) * page_size < total:
        nav.append(InlineKeyboardButton(text="بعدی ›", callback_data=f"owner:classes:{page+1}"))
    if nav:
        buttons.append(nav)
    await render_panel(
        message,
        f"🏫 <b>کلاس‌ها</b>{LINE_BREAK}{LINE_BREAK}تعداد کلاس‌های فعال: <b>{total}</b>{LINE_BREAK}"
        "برای مشاهده اطلاعات و ثبت گروه، کلاس موردنظر را انتخاب کنید.",
        InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.regexp(r"^owner:classes(?::[0-9]+)?$"))
async def owner_classes_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer("این بخش فقط در گفت‌وگوی خصوصی در دسترس است.", show_alert=True)
        return
    user = await get_user(session, callback.from_user, private=True)
    if not user.is_platform_admin:
        await callback.answer("دسترسی مالک سامانه لازم است.", show_alert=True)
        return
    page = int((callback.data or "").rsplit(":", 1)[-1]) if ":" in (callback.data or "") else 0
    await callback.answer()
    await delete_panel(callback.message)
    await _render_owner_classes(callback.message, session, page=page)


@router.callback_query(F.data.regexp(r"^owner:class:[0-9]+$"))
async def owner_class_detail(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer("این بخش فقط در گفت‌وگوی خصوصی در دسترس است.", show_alert=True)
        return
    user = await get_user(session, callback.from_user, private=True)
    if not user.is_platform_admin:
        await callback.answer("دسترسی مالک سامانه لازم است.", show_alert=True)
        return
    class_id = int((callback.data or "").rsplit(":", 1)[-1])
    context = await get_class_with_school(session, class_id)
    if context is None:
        await callback.answer("کلاس فعال پیدا نشد.", show_alert=True)
        return
    cls, year, school = context
    group_status = (
        f"متصل: <b>{escape(cls.group_chat_title or str(cls.group_chat_id))}</b>{LINE_BREAK}"
        f"شناسه گروه: <code>{cls.group_chat_id}</code>"
        if cls.group_chat_id is not None else "هنوز گروهی ثبت نشده است."
    )
    text = (
        f"🏫 <b>{escape(cls.name)}</b>{LINE_BREAK}{LINE_BREAK}"
        f"مدرسه: {escape(school.name)}{LINE_BREAK}"
        f"سال تحصیلی: {escape(year.name)}{LINE_BREAK}"
        f"پایه: {escape(cls.grade)}{LINE_BREAK}"
        f"شعبه: {escape(cls.section or '—')}{LINE_BREAK}"
        f"وضعیت: {'فعال' if cls.is_active else 'غیرفعال'}{LINE_BREAK}{LINE_BREAK}"
        f"🔗 <b>وضعیت گروه</b>{LINE_BREAK}{group_status}"
    )
    await callback.answer()
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 ثبت / تغییر گروه", callback_data=f"group:select:{class_id}", style="success")],
            [InlineKeyboardButton(text="‹ بازگشت به کلاس‌ها", callback_data="owner:classes:0")],
        ]),
    )


@router.message(F.text.in_({"📅 تقویم کلاس", "تقویم کلاس"}))
async def btn_calendar(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user, private=True)
    await _open_calendar(message, session, user)


@router.message(F.text.in_({"📝 امتحانات", "امتحانات"}))
async def btn_exams(message: Message, session: AsyncSession) -> None:
    if message.chat.type == "private":
        await ask_class(message, session, "exams", await get_user(session, message.from_user, private=True))


@router.message(F.text.in_({"📚 تکالیف", "تکالیف"}))
async def btn_homework(message: Message, session: AsyncSession) -> None:
    if message.chat.type == "private":
        await ask_class(message, session, "homework", await get_user(session, message.from_user, private=True))


@router.message(F.text.in_({"📋 گزارش‌های کلاسی", "گزارش‌های کلاسی"}))
async def btn_reports(message: Message, session: AsyncSession) -> None:
    if message.chat.type == "private":
        await ask_class(message, session, "today", await get_user(session, message.from_user, private=True))


async def _open_calendar(message: Message, session: AsyncSession, user: User) -> None:
    classes = await classes_for_user(session, user)
    if not classes:
        await message.answer("هنوز عضو کلاسی نیستید. برای مشاهده تقویم، ابتدا با کد دعوت وارد یک کلاس شوید.")
    elif len(classes) == 1:
        from classmate.bot.handlers.phase1 import _render_calendar

        await _render_calendar(message, session, classes[0][0], classes[0][1])
    else:
        await render_panel(message, "🏫 کلاس موردنظر برای مشاهده تقویم را انتخاب کنید:", class_picker(classes, action="calendar"))


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
        "از گزینه‌های «📊 وضعیت امروز»، «📅 تقویم کلاس» و «📣 اطلاعیه‌ها» برای پیگیری اخبار کلاس استفاده کنید.\n\n"
        "🚧 ⌯ اگه کارت به مشکل خورده یا پیشنهادی برای ارتقا ربات داری میتونی از طریق آیدی زیر با ما در ارتباط باشی🤝🏻\n"
        "🗣️ @Mizanup",
        reply_markup=await menu_for(
            session,
            await get_user(session, message.from_user, private=message.chat.type == "private"),
        ),
    )


@router.message(F.text.in_({"🆘 پشتیبانی", "پشتیبانی"}))
async def btn_support(message: Message, session: AsyncSession) -> None:
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    await message.answer(
        "اگر مشکلی در دریافت برنامه، امتحان، گزارش یا اطلاعیه داری، با پشتیبانی در ارتباط باش:\n"
        "🗣️ @Mizanup",
        reply_markup=await menu_for(session, user),
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


@router.callback_query(F.data == "ui:back")
async def cb_back(callback: CallbackQuery, session: AsyncSession) -> None:
    """Return to the information panel without exposing a main-menu button."""
    if callback.message is None:
        return
    await delete_panel(callback.message)
    await callback.message.answer("📋 <b>اطلاعات کلاس</b>", reply_markup=information_menu())
    await callback.answer()


@router.callback_query(F.data.startswith("edu:notice:"))
async def cb_education_notice(callback: CallbackQuery) -> None:
    await callback.answer(
        "⌯ این دکمه ها فعلا جهت نمایش هستند و فعالشون نکردیم❗",
        show_alert=True,
    )


@router.callback_query(F.data.startswith("info:"))
async def cb_information(callback: CallbackQuery, session: AsyncSession) -> None:
    """Open read-only class information panels from inline buttons."""
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer("این بخش فقط در گفت‌وگوی خصوصی در دسترس است.", show_alert=True)
        return
    action = callback.data.split(":", 1)[1]
    user = await get_user(session, callback.from_user, private=True)
    if action == "calendar":
        await delete_panel(callback.message)
        await _open_calendar(callback.message, session, user)
    elif action in {"exams", "homework", "reports"}:
        await delete_panel(callback.message)
        await ask_class(callback.message, session, "today" if action == "reports" else action, user)
    elif action == "announcements":
        await delete_panel(callback.message)
        await announcements(callback.message, session)
    await callback.answer()


@router.callback_query(F.data.startswith("menu:"))
async def cb_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None:
        return
    action = callback.data.split(":", 1)[1]
    if action == "education":
        await render_panel(callback.message, "📋 <b>اطلاعات کلاس</b>", information_menu())
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
