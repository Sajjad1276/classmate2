"""Multi-step FSM wizards — no slash commands needed."""

import logging
from datetime import date, datetime, time as dtime, timezone
from html import escape
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyKeyboardRemove
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.bot.keyboards import (
    cancel_kb,
    class_picker,
    connect_group_kb,
    day_picker,
    group_request_kb,
    school_picker,
    skip_or_cancel_kb,
    subject_picker,
)
from classmate.bot.handlers.ui import delete_panel, classes_for_user, get_user, menu_for, render_panel
from classmate.bot.states import (
    ClassForm,
    ConnectForm,
    ExamForm,
    HomeworkForm,
    JoinForm,
    ReportForm,
    SchoolForm,
    SubjectForm,
    TimetableForm,
)
from classmate.core.enums import RecordStatus, Role
from classmate.db.models import (
    AcademicYear,
    ClassGroup,
    ClassMembership,
    ClassSubject,
    School,
    SchoolMembership,
    Subject,
    TimetableEntry,
)
from classmate.domain.dates import parse_school_date, parse_school_datetime
from classmate.services.permissions import (
    activate_student_membership,
    code_expiry,
    get_class_with_school,
    connect_group_by_code,
    hash_code,
    can_approve,
    is_class_manager,
    is_school_admin,
    make_code,
    user_can_access_class,
)
from classmate.services.records import (
    add_subject,
    create_daily_report,
    create_exam,
    create_homework,
    get_group_context,
)

router = Router(name="wizards")
logger = logging.getLogger(__name__)


async def _require_private_callback(callback: CallbackQuery) -> bool:
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer(
            "این عملیات فقط از گفت‌وگوی خصوصی ربات در دسترس است.",
            show_alert=True,
        )
        return False
    return True


WEEK_NAMES = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]


def _safe_html_preview(value: str, limit: int = 3000) -> str:
    """Escape text without cutting an HTML entity at Telegram's message limit."""
    parts: list[str] = []
    used = 0
    for character in value:
        escaped = escape(character)
        if used + len(escaped) > limit:
            return "".join(parts) + "…"
        parts.append(escaped)
        used += len(escaped)
    return "".join(parts)


async def _subjects(session: AsyncSession, class_id: int) -> list[tuple[int, str]]:
    rows = (
        await session.execute(
            select(Subject.id, Subject.name)
            .join(ClassSubject, ClassSubject.subject_id == Subject.id)
            .where(ClassSubject.class_id == class_id)
            .order_by(Subject.name)
        )
    ).all()
    return [(r[0], r[1]) for r in rows]


# ── Entry from inline menus ─────────────────────────────────────────


@router.callback_query(F.data == "wiz:hw")
async def entry_hw(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not await _require_private_callback(callback):
        return
    await delete_panel(callback.message)
    user = await get_user(session, callback.from_user, private=True)
    classes = [
        row for row in await classes_for_user(session, user)
        if await is_class_manager(session, user, row[0])
    ]
    if not classes:
        await callback.message.answer("برای ثبت تکلیف از این بخش، باید مدیر یک کلاس فعال باشید.")
        await callback.answer()
        return
    if len(classes) == 1:
        await start_homework_after_class(callback.message, state, session, classes[0][0])
    else:
        await callback.message.answer(
            "ثبت تکلیف — کلاس را انتخاب کن:",
            reply_markup=class_picker(classes, action="hw"),
        )
    await callback.answer()


@router.callback_query(F.data == "wiz:exam")
async def entry_exam(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not await _require_private_callback(callback):
        return
    await delete_panel(callback.message)
    user = await get_user(session, callback.from_user, private=True)
    classes = [
        row for row in await classes_for_user(session, user)
        if await is_class_manager(session, user, row[0])
    ]
    if not classes:
        await callback.message.answer("برای ثبت امتحان از این بخش، باید مدیر یک کلاس فعال باشید.")
        await callback.answer()
        return
    if len(classes) == 1:
        await start_exam_after_class(callback.message, state, session, classes[0][0])
    else:
        await callback.message.answer(
            "ثبت امتحان — کلاس:", reply_markup=class_picker(classes, action="exam")
        )
    await callback.answer()


@router.callback_query(F.data == "wiz:report")
async def entry_report(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not await _require_private_callback(callback):
        return
    await delete_panel(callback.message)
    user = await get_user(session, callback.from_user, private=True)
    classes = await classes_for_user(session, user)
    if not classes:
        await callback.message.answer("برای ادامه، ابتدا عضو یک کلاس شوید.")
        await callback.answer()
        return
    if len(classes) == 1:
        await start_report_after_class(callback.message, state, session, classes[0][0])
    else:
        await callback.message.answer(
            "ثبت گزارش — کلاس:", reply_markup=class_picker(classes, action="report")
        )
    await callback.answer()


@router.callback_query(F.data == "mgr:codes")
async def entry_codes(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _require_private_callback(callback):
        return
    await delete_panel(callback.message)
    user = await get_user(session, callback.from_user, private=bool(callback.message and callback.message.chat.type == "private"))
    classes = [
        row for row in await classes_for_user(session, user)
        if await is_class_manager(session, user, row[0])
    ]
    if not classes:
        await callback.message.answer("کلاسی برای مدیریت در دسترس نیست.")
        await callback.answer()
        return
    await callback.message.answer(
        "کد برای 🏫 کلاس موردنظر را انتخاب کنید:", reply_markup=class_picker(classes, action="codes")
    )
    await callback.answer()


@router.callback_query(F.data == "mgr:subject")
async def entry_subject(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _require_private_callback(callback):
        return
    await delete_panel(callback.message)
    user = await get_user(session, callback.from_user, private=bool(callback.message and callback.message.chat.type == "private"))
    classes = [
        row for row in await classes_for_user(session, user)
        if await is_class_manager(session, user, row[0])
    ]
    if not classes:
        await callback.message.answer("کلاسی در دسترس نیست.")
        await callback.answer()
        return
    await callback.message.answer(
        "درس برای 🏫 کلاس موردنظر را انتخاب کنید:", reply_markup=class_picker(classes, action="subject")
    )
    await callback.answer()


@router.callback_query(F.data == "mgr:timetable")
async def entry_tt(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _require_private_callback(callback):
        return
    await delete_panel(callback.message)
    user = await get_user(session, callback.from_user, private=bool(callback.message and callback.message.chat.type == "private"))
    classes = [
        row for row in await classes_for_user(session, user)
        if await is_class_manager(session, user, row[0])
    ]
    if not classes:
        await callback.message.answer("کلاسی در دسترس نیست.")
        await callback.answer()
        return
    await callback.message.answer(
        "برنامه 🏫 کلاس موردنظر را انتخاب کنید:", reply_markup=class_picker(classes, action="timetable")
    )
    await callback.answer()


@router.callback_query(F.data.startswith("mgr:connect_group"))
async def entry_group(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not await _require_private_callback(callback):
        return
    """Start official Telegram chat selection for a class manager."""
    await delete_panel(callback.message)
    user = await get_user(session, callback.from_user, private=bool(callback.message and callback.message.chat.type == "private"))
    parts = callback.data.split(":")
    if len(parts) == 3:
        class_id = int(parts[2])
        if not await is_class_manager(session, user, class_id):
            await callback.answer("فقط مدیر کلاس می‌تواند گروه را متصل کند.", show_alert=True)
            return
        await start_group_after_class(callback.message, state, class_id)
        await callback.answer()
        return

    classes = [
        row for row in await classes_for_user(session, user)
        if await is_class_manager(session, user, row[0])
    ]
    if not classes:
        await callback.message.answer("شما مدیر هیچ کلاسی نیستید.")
    elif len(classes) == 1:
        await start_group_after_class(callback.message, state, classes[0][0])
    else:
        await callback.message.answer(
            "🏫 کلاس موردنظر را انتخاب کنید:",
            reply_markup=class_picker(classes, action="group"),
        )
    await callback.answer()


@router.callback_query(F.data == "adm:school")
async def entry_school(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not await _require_private_callback(callback):
        return
    user = await get_user(session, callback.from_user, private=True)
    if not user.is_active or not user.is_platform_admin:
        await callback.answer("دسترسی مدیر فعال سامانه لازم است.", show_alert=True)
        return
    await delete_panel(callback.message)
    await state.set_state(SchoolForm.name)
    await callback.message.answer("نام مدرسه را ارسال کنید:", reply_markup=cancel_kb())
    await callback.answer()


@router.callback_query(F.data == "adm:class")
async def entry_class(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not await _require_private_callback(callback):
        return
    await delete_panel(callback.message)
    user = await get_user(session, callback.from_user, private=True)
    if not user.is_active:
        await callback.answer("حساب کاربری شما غیرفعال است.", show_alert=True)
        return
    q = select(School).where(School.is_active.is_(True)).order_by(School.name)
    if not user.is_platform_admin:
        q = (
            select(School)
            .join(SchoolMembership, SchoolMembership.school_id == School.id)
            .where(
                SchoolMembership.user_id == user.id,
                SchoolMembership.role == Role.SCHOOL_ADMIN,
                SchoolMembership.is_active.is_(True),
                School.is_active.is_(True),
            )
        )
    schools = [(s.id, s.name) for s in (await session.execute(q)).scalars().all()]
    if not schools:
        await callback.message.answer("مدرسه‌ای برای ایجاد کلاس در دسترس شما نیست.")
        await callback.answer()
        return
    await state.set_state(ClassForm.school_id)
    await callback.message.answer("مدرسه موردنظر را انتخاب کنید:", reply_markup=school_picker(schools))
    await callback.answer()


# ── Helpers called from ui.pick ─────────────────────────────────────


async def start_homework_after_class(message, state, session, class_id: int) -> None:
    await state.update_data(class_id=class_id)
    subs = await _subjects(session, class_id)
    if not subs:
        await message.answer(
            "این کلاس هنوز درسی ندارد. از بخش «مدیریت کلاس → مدیریت درس‌ها» یک درس اضافه کنید."
        )
        await state.clear()
        return
    await state.set_state(HomeworkForm.subject)
    await message.answer("درس موردنظر را انتخاب کنید:", reply_markup=subject_picker(subs))


async def start_exam_after_class(message, state, session, class_id: int) -> None:
    await state.update_data(class_id=class_id)
    subs = await _subjects(session, class_id)
    if not subs:
        await message.answer("ابتدا از بخش «مدیریت کلاس» یک درس اضافه کنید.")
        await state.clear()
        return
    await state.set_state(ExamForm.subject)
    await message.answer("درس امتحان را انتخاب کنید:", reply_markup=subject_picker(subs))


async def start_report_after_class(message, state, session, class_id: int) -> None:
    if message.chat.type != "private":
        await state.clear()
        await message.answer(
            "برای حفظ حریم خصوصی، ثبت گزارش روزانه فقط از گفت‌وگوی خصوصی ربات انجام می‌شود."
        )
        return
    await state.update_data(class_id=class_id)
    await state.set_state(ReportForm.body)
    await message.answer("متن گزارش امروز را ارسال کنید:", reply_markup=cancel_kb())


async def start_subject_after_class(message, state, class_id: int) -> None:
    await state.update_data(class_id=class_id)
    await state.set_state(SubjectForm.name)
    await message.answer("نام درس را ارسال کنید؛ برای نمونه: ریاضی", reply_markup=cancel_kb())


async def start_timetable_after_class(message, state, class_id: int) -> None:
    await state.update_data(class_id=class_id)
    await state.set_state(TimetableForm.day)
    await message.answer("روز هفته را انتخاب کنید:", reply_markup=day_picker())


async def start_group_after_class(message, state, class_id: int) -> None:
    await state.update_data(class_id=class_id)
    await state.set_state(ConnectForm.chat)
    await message.answer(
        "🔗 <b>اتصال گروه کلاس</b>\n\n"
        "گروه را از دکمه زیر انتخاب کنید. شما و ربات باید در آن گروه دسترسی مدیریتی داشته باشید.",
        reply_markup=group_request_kb(request_id=class_id),
    )


async def issue_codes(message, session, user, class_id: int) -> None:
    if not await is_class_manager(session, user, class_id):
        await message.answer("ساخت کد فقط برای مدیر کلاس مجاز است.")
        return
    cls = await session.get(ClassGroup, class_id)
    if not cls:
        await message.answer("کلاس موردنظر پیدا نشد. لطفاً دوباره تلاش کنید.")
        return
    group_code = make_code(8)
    join_code = make_code(8)
    cls.group_link_code_hash = hash_code(group_code)
    cls.group_link_code_expires_at = code_expiry(30)
    cls.member_join_code_hash = hash_code(join_code)
    cls.member_join_code_expires_at = code_expiry(60 * 24 * 7)
    await session.commit()
    await message.answer(
        f"کدهای <b>{escape(cls.name)}</b>:\n\n"
        f"🔗 کد اتصال گروه (اعتبار: ۳۰ دقیقه):\n<code>{group_code}</code>\n\n"
        f"🔑 کد دعوت دانش‌آموز (اعتبار: ۷ روز):\n<code>{join_code}</code>\n\n"
        f"کد اتصال را در گروه کلاس، به‌صورت یک پیام معمولی ارسال کنید.\n"
        f"دانش‌آموز می‌تواند با انتخاب «ورود با کد دعوت» به کلاس ملحق شود."
    )


# ── Homework wizard steps ───────────────────────────────────────────


@router.callback_query(HomeworkForm.subject, F.data.startswith("subj:"))
async def hw_subject(callback: CallbackQuery, state: FSMContext) -> None:
    await delete_panel(callback.message)
    sid = int(callback.data.split(":")[1])
    await state.update_data(subject_id=sid)
    await state.set_state(HomeworkForm.title)
    await callback.message.answer("عنوان تکلیف را ارسال کنید:", reply_markup=cancel_kb())
    await callback.answer()


@router.message(HomeworkForm.title, F.text)
async def hw_title(message: Message, state: FSMContext) -> None:
    if message.text == "❌ انصراف":
        return
    await state.update_data(title=message.text.strip()[:180])
    await state.set_state(HomeworkForm.description)
    await message.answer("توضیحات تکلیف را ارسال کنید:", reply_markup=cancel_kb())


@router.message(HomeworkForm.description, F.text)
async def hw_desc(message: Message, state: FSMContext) -> None:
    if message.text == "❌ انصراف":
        return
    await state.update_data(description=message.text.strip())
    await state.set_state(HomeworkForm.due)
    await message.answer(
        "موعد (مثلاً ۱۴۰۵/۰۸/۰۱) یا «بدون موعد»:\n",
        reply_markup=skip_or_cancel_kb(),
    )


@router.message(HomeworkForm.due, F.text)
async def hw_due(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if message.text == "❌ انصراف":
        return
    data = await state.get_data()
    due = None
    if message.text and message.text.strip() not in {"⏭ بدون تعیین موعد", "بدون موعد"}:
        try:
            due = parse_school_date(message.text)
        except ValueError as e:
            await message.answer(str(e))
            return

    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    if not data.get("class_id") or not await user_can_access_class(session, user, data["class_id"]):
        await state.clear()
        await message.answer("دسترسی شما به این کلاس فعال نیست.", reply_markup=await menu_for(session, user))
        return
    subject = await session.get(Subject, data["subject_id"])
    if subject is None:
        await message.answer("درس انتخاب‌شده معتبر نیست.")
        await state.clear()
        return

    record = await create_homework(
        session,
        data["class_id"],
        subject,
        data["title"],
        data["description"],
        due,
        user.id,
    )
    pending = record.status == RecordStatus.PENDING
    await session.commit()

    await state.clear()
    await message.answer(
        "✅ تکلیف ثبت و منتشر شد." if not pending else "✅ تکلیف ثبت شد و در انتظار بررسی و تأیید است.",
        reply_markup=await menu_for(session, user),
    )


# ── Exam wizard ─────────────────────────────────────────────────────


@router.callback_query(ExamForm.subject, F.data.startswith("subj:"))
async def exam_subject(callback: CallbackQuery, state: FSMContext) -> None:
    await delete_panel(callback.message)
    await state.update_data(subject_id=int(callback.data.split(":")[1]))
    await state.set_state(ExamForm.title)
    await callback.message.answer("عنوان امتحان را ارسال کنید:", reply_markup=cancel_kb())
    await callback.answer()


@router.message(ExamForm.title, F.text)
async def exam_title(message: Message, state: FSMContext) -> None:
    if message.text == "❌ انصراف":
        return
    await state.update_data(title=message.text.strip()[:180])
    await state.set_state(ExamForm.scope)
    await message.answer("محدوده امتحان (فصل‌ها یا صفحات) را ارسال کنید:", reply_markup=cancel_kb())


@router.message(ExamForm.scope, F.text)
async def exam_scope(message: Message, state: FSMContext) -> None:
    if message.text == "❌ انصراف":
        return
    await state.update_data(scope=message.text.strip())
    await state.set_state(ExamForm.when)
    await message.answer(
        "زمان برگزاری امتحان را ارسال کنید:\nمثال: <code>۱۴۰۵/۰۸/۱۰ ۱۰:۰۰</code>",
        reply_markup=cancel_kb(),
    )


@router.message(ExamForm.when, F.text)
async def exam_when(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if message.text == "❌ انصراف":
        return
    data = await state.get_data()
    ctx = await get_class_with_school(session, data["class_id"])
    if ctx is None:
        await message.answer("کلاس انتخاب‌شده معتبر نیست.")
        await state.clear()
        return
    _, _, school = ctx
    try:
        scheduled = parse_school_datetime(message.text, school.timezone)
    except ValueError as e:
        await message.answer(str(e))
        return

    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    if not data.get("class_id") or not await user_can_access_class(session, user, data["class_id"]):
        await state.clear()
        await message.answer("دسترسی شما به این کلاس فعال نیست.", reply_markup=await menu_for(session, user))
        return
    subject = await session.get(Subject, data["subject_id"])
    if subject is None:
        await state.clear()
        await message.answer("درس انتخاب‌شده معتبر نیست.", reply_markup=await menu_for(session, user))
        return
    record = await create_exam(
        session,
        data["class_id"],
        subject,
        data["title"],
        data["scope"],
        "quiz",
        scheduled,
        user.id,
    )
    pending = record.status == RecordStatus.PENDING
    await session.commit()
    await state.clear()
    await message.answer(
        "✅ امتحان ثبت و منتشر شد." if not pending else "✅ امتحان ثبت شد و در انتظار بررسی و تأیید است.",
        reply_markup=await menu_for(session, user),
    )


# ── Report wizard ───────────────────────────────────────────────────


@router.message(ReportForm.body, F.text)
async def report_body(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if message.chat.type != "private":
        await state.clear()
        await message.answer("ثبت گزارش روزانه فقط در گفت‌وگوی خصوصی ربات انجام می‌شود.")
        return
    if message.text == "❌ انصراف":
        await state.clear()
        user = await get_user(session, message.from_user, private=True)
        await message.answer("ثبت گزارش لغو شد.", reply_markup=await menu_for(session, user))
        return

    report_text = (message.text or "").strip()
    if not report_text:
        await message.answer("متن گزارش نمی‌تواند خالی باشد. متن را دوباره ارسال کنید.")
        return

    data = await state.get_data()
    user = await get_user(session, message.from_user, private=True)
    class_id = data.get("class_id")
    if not class_id or not await user_can_access_class(session, user, class_id):
        await state.clear()
        await message.answer(
            "دسترسی شما به این کلاس فعال نیست.",
            reply_markup=await menu_for(session, user),
        )
        return
    if await get_class_with_school(session, class_id) is None:
        await state.clear()
        await message.answer("کلاس انتخاب‌شده دیگر فعال نیست.", reply_markup=await menu_for(session, user))
        return

    await state.update_data(
        report_body=report_text,
        report_owner_telegram_id=message.from_user.id,
    )
    await state.set_state(ReportForm.confirm)
    is_operator = await can_approve(session, user, class_id)
    preview = _safe_html_preview(report_text)
    if len(escape(report_text)) > len(preview):
        preview += "\n… <i>ادامه متن در نسخه نهایی نگهداری می‌شود.</i>"
    action_text = (
        "✅ تأیید و انتشار گزارش"
        if is_operator
        else "📨 تأیید و ارسال برای اپراتور"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=action_text, callback_data="rpt:confirm", style="success")],
            [
                InlineKeyboardButton(text="✏️ ویرایش متن", callback_data="rpt:edit"),
                InlineKeyboardButton(text="❌ لغو گزارش", callback_data="rpt:cancel", style="danger"),
            ],
        ]
    )
    await render_panel(message,
        "📋 <b>پیش‌نمایش گزارش روزانه</b>\n\n"
        f"{preview}\n\n"
        + (
            "پس از تأیید شما، گزارش در گروه منتشر می‌شود."
            if is_operator
            else "پس از تأیید شما، گزارش فقط برای بررسی اپراتور ارسال می‌شود و تا آن زمان در گروه منتشر نخواهد شد."
        ),
        keyboard,
    )


@router.callback_query(ReportForm.confirm, F.data == "rpt:edit")
async def report_edit(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("report_owner_telegram_id") != callback.from_user.id:
        await callback.answer("این پیش‌نویس متعلق به شما نیست.", show_alert=True)
        return
    if callback.message:
        await delete_panel(callback.message)
        await callback.message.answer(
            "✏️ متن اصلاح‌شده گزارش را ارسال کنید. پیش از انتشار دوباره پیش‌نمایش خواهید دید:",
            reply_markup=cancel_kb(),
        )
    await state.set_state(ReportForm.body)
    await callback.answer()


@router.callback_query(ReportForm.confirm, F.data == "rpt:cancel")
async def report_cancel(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    if data.get("report_owner_telegram_id") != callback.from_user.id:
        await callback.answer("این پیش‌نویس متعلق به شما نیست.", show_alert=True)
        return
    await state.clear()
    user = await get_user(session, callback.from_user, private=True)
    if callback.message:
        await delete_panel(callback.message)
        await callback.message.answer("گزارش لغو شد و هیچ پیامی منتشر نشد.", reply_markup=await menu_for(session, user))
    await callback.answer("گزارش لغو شد.")


@router.callback_query(ReportForm.confirm, F.data == "rpt:confirm")
async def report_confirm(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    if data.get("report_owner_telegram_id") != callback.from_user.id:
        await callback.answer("این پیش‌نویس متعلق به شما نیست.", show_alert=True)
        return
    report_text = data.get("report_body")
    class_id = data.get("class_id")
    if not isinstance(report_text, str) or not report_text.strip() or not class_id:
        await state.clear()
        await callback.answer("پیش‌نویس معتبر نیست؛ دوباره شروع کنید.", show_alert=True)
        return

    user = await get_user(session, callback.from_user, private=True)
    if not await user_can_access_class(session, user, int(class_id)):
        await state.clear()
        await callback.answer("دسترسی شما به این کلاس دیگر فعال نیست.", show_alert=True)
        if callback.message:
            await callback.message.edit_text("ثبت گزارش انجام نشد؛ دسترسی کلاس فعال نیست.")
        return
    context = await get_class_with_school(session, int(class_id))
    if context is None:
        await state.clear()
        await callback.answer("کلاس انتخاب‌شده دیگر فعال نیست.", show_alert=True)
        if callback.message:
            await callback.message.edit_text("ثبت گزارش انجام نشد؛ کلاس دیگر فعال نیست.")
        return

    _, _, school = context
    local_today = datetime.now(ZoneInfo(school.timezone)).date()
    try:
        record = await create_daily_report(
            session, int(class_id), local_today, report_text.strip(), user.id
        )
        await session.commit()
    except (ValueError, PermissionError) as exc:
        await state.clear()
        await callback.answer(str(exc), show_alert=True)
        if callback.message:
            await callback.message.edit_text(f"ثبت گزارش انجام نشد: {escape(str(exc))}")
        return

    pending = record.status == RecordStatus.PENDING
    await state.clear()
    if callback.message:
        await callback.message.edit_text(
            (
                "🕓 <b>گزارش برای بررسی ثبت شد</b>\n\n"
                "درخواست در پنل «موارد در انتظار بررسی» قرار گرفت. تا تأیید اپراتور، هیچ پیامی در گروه منتشر نمی‌شود."
                if pending
                else "✅ <b>گزارش تأیید و منتشر شد</b>\n\n"
                "گزارش با تأیید شما در سامانه ثبت شد و برای گروه متصل در صف انتشار قرار گرفت."
            )
        )
        await callback.message.answer("منوی اصلی:", reply_markup=await menu_for(session, user))
    await callback.answer("گزارش ثبت شد.")


# ── Join ────────────────────────────────────────────────────────────


@router.message(JoinForm.code, F.text)
async def join_code(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if message.text == "❌ انصراف":
        return
    code = message.text.strip().upper()
    user = await get_user(session, message.from_user, private=True)
    if not user.is_active:
        await message.answer("حساب کاربری شما غیرفعال است.")
        return
    cls = await session.scalar(
        select(ClassGroup)
        .join(AcademicYear, ClassGroup.academic_year_id == AcademicYear.id)
        .join(School, AcademicYear.school_id == School.id)
        .where(
            ClassGroup.member_join_code_hash == hash_code(code),
            ClassGroup.is_active.is_(True),
            School.is_active.is_(True),
        )
    )
    if cls is None:
        await message.answer("کد واردشده معتبر نیست. لطفاً دوباره تلاش کنید یا عملیات را لغو کنید.")
        return
    from datetime import datetime

    if (
        cls.member_join_code_expires_at is None
        or cls.member_join_code_expires_at.replace(tzinfo=cls.member_join_code_expires_at.tzinfo or timezone.utc)
        <= datetime.now(timezone.utc)
    ):
        await message.answer("این کد منقضی شده است. لطفاً کد جدیدی دریافت کنید.")
        await state.clear()
        await message.answer("منو:", reply_markup=await menu_for(session, user))
        return

    await activate_student_membership(session, cls.id, user.id)
    await session.commit()
    await state.clear()
    await message.answer(
        f"✅ با موفقیت به کلاس <b>{escape(cls.name)}</b> ملحق شدید.",
        reply_markup=await menu_for(session, user),
    )


@router.message(ConnectForm.chat, F.chat_shared)
async def group_shared(message: Message, state: FSMContext, session: AsyncSession) -> None:
    shared = message.chat_shared
    if shared is None:
        return
    data = await state.get_data()
    class_id = data.get("class_id")
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    if not class_id or not await is_class_manager(session, user, class_id):
        await state.clear()
        await message.answer("دسترسی اتصال گروه برای شما فعال نیست.", reply_markup=ReplyKeyboardRemove())
        return

    try:
        chat = await message.bot.get_chat(shared.chat_id)
        bot_user = await message.bot.me()
        bot_member = await message.bot.get_chat_member(shared.chat_id, bot_user.id)
        user_member = await message.bot.get_chat_member(shared.chat_id, message.from_user.id)
    except Exception:
        logger.warning("Could not verify selected group %s", shared.chat_id, exc_info=True)
        await message.answer(
            "ربات به گروه انتخاب‌شده دسترسی ندارد. ابتدا ربات را به گروه اضافه و مدیر کنید، سپس دوباره تلاش کنید."
        )
        return

    if bot_member.status not in {"administrator", "creator"}:
        await message.answer("ربات باید در گروه مدیر باشد؛ سپس دوباره گروه را انتخاب کنید.")
        return
    if user_member.status not in {"administrator", "creator"}:
        await message.answer("فقط مالک یا مدیر گروه می‌تواند آن را به کلاس متصل کند.")
        return

    conflict = await session.scalar(
        select(ClassGroup.id).where(
            ClassGroup.group_chat_id == shared.chat_id,
            ClassGroup.id != class_id,
        )
    )
    if conflict is not None:
        await state.clear()
        await message.answer("این گروه قبلاً به کلاس دیگری متصل شده است.", reply_markup=ReplyKeyboardRemove())
        return

    class_group = await session.get(ClassGroup, class_id)
    if class_group is None or not class_group.is_active:
        await state.clear()
        await message.answer("کلاس موردنظر پیدا نشد.", reply_markup=ReplyKeyboardRemove())
        return
    if class_group.group_chat_id is not None and class_group.group_chat_id != shared.chat_id:
        await state.clear()
        await message.answer("این کلاس از قبل به گروه دیگری متصل است. ابتدا اتصال قبلی را لغو کنید.", reply_markup=ReplyKeyboardRemove())
        return
    class_group.group_chat_id = shared.chat_id
    class_group.group_chat_title = shared.title or chat.title or shared.username or "گروه کلاس"
    await session.commit()
    await state.clear()
    await message.answer(
        f"✅ گروه «{escape(class_group.group_chat_title)}» به کلاس <b>{escape(class_group.name)}</b> متصل شد.\n"
        "اعضای گروه می‌توانند «منو» را ارسال کنند تا پنل خدمات کلاس را ببینند.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer("منوی اصلی:", reply_markup=await menu_for(session, user))


@router.message(ConnectForm.chat, F.text)
async def group_shared_text(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if message.text == "❌ انصراف":
        await state.clear()
        user = await get_user(session, message.from_user, private=message.chat.type == "private")
        await message.answer("اتصال گروه لغو شد.", reply_markup=await menu_for(session, user))
        return
    await message.answer("برای اتصال، دکمه «✅ انتخاب گروه کلاس» را بزنید یا «❌ انصراف» را انتخاب کنید.")


# ── Group connect: any text that looks like a code in a group ───────


@router.message(F.chat.type.in_({"group", "supergroup"}), F.text.regexp(r"^[A-HJ-NP-Z2-9]{6,10}$"))
async def group_connect_code(message: Message, session: AsyncSession) -> None:
    """Connect a class group through the same guarded service as /connect."""
    if message.from_user is None or message.text is None:
        return

    user = await get_user(session, message.from_user, private=False)
    try:
        bot_user = await message.bot.me()
        bot_member = await message.bot.get_chat_member(message.chat.id, bot_user.id)
        requester_member = await message.bot.get_chat_member(
            message.chat.id, message.from_user.id
        )
    except Exception:
        logger.warning(
            "Could not verify group connection permissions for chat %s",
            message.chat.id,
            exc_info=True,
        )
        await message.answer(
            "امکان بررسی مدیران گروه وجود ندارد. مطمئن شوید ربات در گروه مدیر است و دوباره تلاش کنید."
        )
        return

    try:
        class_group = await connect_group_by_code(
            session,
            code=message.text.strip().upper(),
            chat_id=message.chat.id,
            chat_title=message.chat.title,
            actor=user,
            bot_is_admin=bot_member.status in {"administrator", "creator"},
            actor_is_admin=requester_member.status in {"administrator", "creator"},
        )
    except (PermissionError, ValueError) as exc:
        await message.answer(str(exc))
        return

    await message.answer(
        f"✅ گروه به کلاس <b>{escape(class_group.name)}</b> متصل شد."
    )


# ── School form ─────────────────────────────────────────────────────


@router.message(SchoolForm.name, F.text)
async def school_name(message: Message, state: FSMContext) -> None:
    if message.text == "❌ انصراف":
        return
    await state.update_data(school_name=message.text.strip()[:180])
    await state.set_state(SchoolForm.year)
    await message.answer("نام سال تحصیلی (مثلاً ۱۴۰۵-۱۴۰۶):", reply_markup=cancel_kb())


@router.message(SchoolForm.year, F.text)
async def school_year(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if message.text == "❌ انصراف":
        return
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    if not user.is_platform_admin:
        await message.answer("این عملیات فقط برای مدیر سامانه مجاز است.")
        await state.clear()
        return
    data = await state.get_data()
    school = School(name=data["school_name"], timezone="Asia/Tehran")
    session.add(school)
    await session.flush()
    year = AcademicYear(school_id=school.id, name=message.text.strip()[:40], is_current=True)
    session.add(year)
    session.add(SchoolMembership(school_id=school.id, user_id=user.id, role=Role.SCHOOL_ADMIN))
    await state.clear()
    await message.answer(
        f"✅ مدرسه <b>{escape(school.name)}</b> ساخته شد.\n"
        f"حالا از «مدیریت سامانه → ساخت کلاس» کلاس بساز.",
        reply_markup=await menu_for(session, user),
    )


# ── Class form ──────────────────────────────────────────────────────


@router.callback_query(ClassForm.school_id, F.data.startswith("sch:"))
async def class_school(callback: CallbackQuery, state: FSMContext) -> None:
    await delete_panel(callback.message)
    await state.update_data(school_id=int(callback.data.split(":")[1]))
    await state.set_state(ClassForm.grade)
    await callback.message.answer("پایه (مثلاً دهم):", reply_markup=cancel_kb())
    await callback.answer()


@router.message(ClassForm.grade, F.text)
async def class_grade(message: Message, state: FSMContext) -> None:
    if message.text == "❌ انصراف":
        return
    await state.update_data(grade=message.text.strip()[:32])
    await state.set_state(ClassForm.section)
    await message.answer("شعبه / شماره کلاس (مثلاً ۱۰۴):", reply_markup=cancel_kb())


@router.message(ClassForm.section, F.text)
async def class_section(message: Message, state: FSMContext) -> None:
    if message.text == "❌ انصراف":
        return
    await state.update_data(section=message.text.strip()[:32])
    await state.set_state(ClassForm.name)
    await message.answer("نام نمایشی کلاس (مثلاً دهم ۱۰۴):", reply_markup=cancel_kb())


@router.message(ClassForm.name, F.text)
async def class_name(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if message.text == "❌ انصراف":
        return
    data = await state.get_data()
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    school_id = data["school_id"]
    if not await is_school_admin(session, user, school_id):
        await message.answer("شما دسترسی انجام این عملیات را ندارید.")
        await state.clear()
        return
    year = await session.scalar(
        select(AcademicYear).where(
            AcademicYear.school_id == school_id, AcademicYear.is_current.is_(True)
        )
    )
    if year is None:
        await message.answer("سال تحصیلی فعالی برای این مدرسه تعریف نشده است.")
        await state.clear()
        return
    cls = ClassGroup(
        academic_year_id=year.id,
        name=message.text.strip()[:80],
        grade=data["grade"],
        section=data.get("section"),
    )
    session.add(cls)
    await session.flush()
    session.add(ClassMembership(class_id=cls.id, user_id=user.id, role=Role.CLASS_MANAGER))
    await state.clear()
    await message.answer(
        f"✅ کلاس <b>{escape(cls.name)}</b> ساخته شد.\n"
        "برای فعال‌سازی خدمات کلاس، همین حالا گروه کلاس را متصل کنید.",
        reply_markup=connect_group_kb(cls.id),
    )


# ── Subject form ────────────────────────────────────────────────────


@router.message(SubjectForm.name, F.text)
async def subject_name(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if message.text == "❌ انصراف":
        return
    data = await state.get_data()
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    class_id = data["class_id"]
    if not await is_class_manager(session, user, class_id):
        await message.answer("این عملیات فقط برای مدیر کلاس مجاز است.")
        await state.clear()
        return
    ctx = await get_class_with_school(session, class_id)
    if not ctx:
        await message.answer("کلاس انتخاب‌شده معتبر نیست.")
        await state.clear()
        return
    _, _, school = ctx
    subj = await add_subject(session, class_id, school.id, message.text.strip())
    await session.commit()
    await state.clear()
    await message.answer(
        f"✅ درس <b>{escape(subj.name)}</b> اضافه شد.",
        reply_markup=await menu_for(session, user),
    )


# ── Timetable form ──────────────────────────────────────────────────


@router.callback_query(TimetableForm.day, F.data.startswith("day:"))
async def tt_day(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    user = await get_user(session, callback.from_user, private=bool(callback.message and callback.message.chat.type == "private"))
    if not data.get("class_id") or not await is_class_manager(session, user, data["class_id"]):
        await state.clear()
        await callback.answer("دسترسی مدیریت این کلاس را ندارید.", show_alert=True)
        return
    try:
        weekday = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("روز انتخاب‌شده معتبر نیست.", show_alert=True)
        return
    if weekday not in range(7):
        await callback.answer("روز انتخاب‌شده معتبر نیست.", show_alert=True)
        return
    await delete_panel(callback.message)
    await state.update_data(weekday=weekday)
    await state.set_state(TimetableForm.period)
    await callback.message.answer("شماره زنگ (۱ تا ۲۰):", reply_markup=cancel_kb())
    await callback.answer()


@router.message(TimetableForm.period, F.text)
async def tt_period(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if message.text == "❌ انصراف":
        return
    try:
        period = int(message.text.strip())
    except ValueError:
        await message.answer("لطفاً شماره زنگ را به‌صورت عدد وارد کنید.")
        return
    if not 1 <= period <= 20:
        await message.answer("شماره زنگ باید بین ۱ تا ۲۰ باشد.")
        return
    data = await state.get_data()
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    if not data.get("class_id") or not await is_class_manager(session, user, data["class_id"]):
        await state.clear()
        await message.answer("این عملیات فقط برای مدیر کلاس مجاز است.")
        return
    await state.update_data(period=period)
    subs = await _subjects(session, data["class_id"])
    if not subs:
        await message.answer("ابتدا از بخش «مدیریت کلاس» یک درس اضافه کنید.")
        await state.clear()
        return
    await state.set_state(TimetableForm.subject)
    await message.answer("درس این زنگ:", reply_markup=subject_picker(subs))


@router.callback_query(TimetableForm.subject, F.data.startswith("subj:"))
async def tt_subject(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    user = await get_user(session, callback.from_user, private=bool(callback.message and callback.message.chat.type == "private"))
    if not data.get("class_id") or not await is_class_manager(session, user, data["class_id"]):
        await state.clear()
        await callback.answer("دسترسی مدیریت این کلاس را ندارید.", show_alert=True)
        return
    try:
        subject_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("درس انتخاب‌شده معتبر نیست.", show_alert=True)
        return
    linked = await session.scalar(select(ClassSubject.id).where(
        ClassSubject.class_id == data["class_id"], ClassSubject.subject_id == subject_id
    ))
    if linked is None:
        await callback.answer("این درس متعلق به کلاس انتخاب‌شده نیست.", show_alert=True)
        return
    await delete_panel(callback.message)
    await state.update_data(subject_id=subject_id)
    await state.set_state(TimetableForm.time_range)
    await callback.message.answer(
        "ساعت شروع-پایان (مثلاً 08:00-08:45):", reply_markup=cancel_kb()
    )
    await callback.answer()


@router.message(TimetableForm.time_range, F.text)
async def tt_time(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if message.text == "❌ انصراف":
        return
    try:
        start_s, end_s = message.text.strip().split("-")
        sh, sm = map(int, start_s.split(":"))
        eh, em = map(int, end_s.split(":"))
        starts, ends = dtime(sh, sm), dtime(eh, em)
        if ends <= starts:
            raise ValueError("ساعت پایان باید بعد از ساعت شروع باشد.")
    except (ValueError, IndexError, TypeError):
        await message.answer("قالب زمان صحیح: 08:00-08:45")
        return

    data = await state.get_data()
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    class_id = data["class_id"]
    if not await is_class_manager(session, user, class_id):
        await message.answer("این عملیات فقط برای مدیر کلاس مجاز است.")
        await state.clear()
        return
    if data.get("weekday") not in range(7) or not 1 <= int(data.get("period", 0)) <= 20:
        await message.answer("روز یا شماره زنگ انتخاب‌شده معتبر نیست.")
        await state.clear()
        return
    linked = await session.scalar(select(ClassSubject.id).where(
        ClassSubject.class_id == class_id,
        ClassSubject.subject_id == data.get("subject_id"),
    ))
    if linked is None:
        await message.answer("درس انتخاب‌شده متعلق به این کلاس نیست.")
        await state.clear()
        return

    existing = await session.scalar(
        select(TimetableEntry).where(
            TimetableEntry.class_id == class_id,
            TimetableEntry.weekday == data["weekday"],
            TimetableEntry.period == data["period"],
        )
    )
    if existing:
        existing.subject_id = data["subject_id"]
        existing.starts_at = starts
        existing.ends_at = ends
    else:
        session.add(
            TimetableEntry(
                class_id=class_id,
                subject_id=data["subject_id"],
                weekday=data["weekday"],
                period=data["period"],
                starts_at=starts,
                ends_at=ends,
            )
        )
    day = WEEK_NAMES[data["weekday"]]
    await session.commit()
    await state.clear()
    await message.answer(
        f"✅ {day} زنگ {data['period']} تنظیم شد.",
        reply_markup=await menu_for(session, user),
    )
