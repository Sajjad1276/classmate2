"""Reply-keyboard and inline UX flows (class picker, menus)."""

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.bot.keyboards import class_picker, main_menu, new_record_menu
from classmate.core.config import get_settings
from classmate.core.enums import Role
from classmate.db.models import AcademicYear, ClassGroup, ClassMembership, School, SchoolMembership, User
from classmate.services.permissions import can_approve, get_class_with_school, is_class_manager
from classmate.services.records import list_exams, list_homework, list_pending_records, list_reports
from classmate.services.users import ensure_user
from classmate.domain.dates import jalali_date, jalali_datetime
from datetime import date
from classmate.db.models import Subject, TimetableEntry

router = Router(name="ui")

WEEKDAYS = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]


async def _user(session: AsyncSession, tg, private: bool = True) -> User:
    settings = get_settings()
    user = await ensure_user(
        session,
        telegram_id=tg.id,
        username=tg.username,
        full_name=tg.full_name or tg.first_name or "User",
        private_chat_started=private,
    )
    if tg.id in settings.bootstrap_admin_id_set:
        user.is_platform_admin = True
    return user


async def _role_flags(session: AsyncSession, user: User) -> tuple[bool, bool, bool]:
    """school_admin-ish, class_manager, approver"""
    if user.is_platform_admin:
        return True, True, True
    school_admin = (
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
    class_manager = Role.CLASS_MANAGER in roles or school_admin
    approver = (
        school_admin
        or class_manager
        or Role.TRUSTED_APPROVER in roles
    )
    return school_admin, class_manager, approver


async def _classes_for_user(session: AsyncSession, user: User) -> list[tuple[int, str, str]]:
    query = (
        select(ClassGroup, School)
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
            (SchoolMembership.school_id == School.id)
            & (SchoolMembership.user_id == user.id)
            & (SchoolMembership.role == Role.SCHOOL_ADMIN)
            & SchoolMembership.is_active.is_(True),
        )
        .where(
            ClassGroup.is_active.is_(True),
            or_(
                ClassMembership.id.is_not(None),
                SchoolMembership.id.is_not(None),
                user.is_platform_admin,
            ),
        )
        .distinct()
        .order_by(ClassGroup.name)
    )
    rows = (await session.execute(query)).all()
    return [(c.id, c.name, s.name) for c, s in rows]


async def _ask_class(message: Message, session: AsyncSession, action: str, user: User) -> None:
    classes = await _classes_for_user(session, user)
    if not classes:
        await message.answer(
            "هنوز به کلاسی وصل نیستی.\n"
            "با /join کد_عضویت عضو شو یا از مدیر کلاس کد بگیر."
        )
        return
    if len(classes) == 1:
        # Auto-select single class
        class_id = classes[0][0]
        await _run_action(message, session, action, class_id, user)
        return
    await message.answer(
        "کدام کلاس؟",
        reply_markup=class_picker(classes, action=action),
    )


async def _run_action(
    target: Message,
    session: AsyncSession,
    action: str,
    class_id: int,
    user: User,
) -> None:
    ctx = await get_class_with_school(session, class_id)
    if ctx is None:
        await target.answer("کلاس پیدا نشد.")
        return
    class_group, _, school = ctx

    if action == "homework":
        rows = await list_homework(session, class_id)
        if not rows:
            await target.answer(f"📚 <b>{escape(class_group.name)}</b>\nهنوز تکلیف تأییدشده‌ای نیست.")
            return
        lines = [f"📚 <b>تکالیف — {escape(class_group.name)}</b>"]
        for hw, subj in rows:
            lines.append(
                f"\n• <b>{escape(subj.name)}</b> — {escape(hw.title)}\n"
                f"  {escape(hw.description)}\n  موعد: {jalali_date(hw.due_on)}"
            )
        await target.answer("\n".join(lines))

    elif action == "exams":
        rows = await list_exams(session, class_id)
        if not rows:
            await target.answer(f"📝 <b>{escape(class_group.name)}</b>\nامتحان آینده‌ای نیست.")
            return
        lines = [f"📝 <b>امتحانات — {escape(class_group.name)}</b>"]
        for exam, subj in rows:
            lines.append(
                f"\n• <b>{escape(subj.name)}</b> — {escape(exam.title)}\n"
                f"  {escape(exam.scope or '—')}\n"
                f"  {jalali_datetime(exam.scheduled_at, school.timezone)}"
            )
        await target.answer("\n".join(lines))

    elif action == "today":
        reports = await list_reports(session, class_id, date.today())
        if not reports:
            await target.answer(
                f"📋 <b>{escape(class_group.name)}</b>\n"
                f"گزارش تأییدشده‌ای برای امروز ({jalali_date(date.today())}) نیست."
            )
            return
        lines = [f"📋 <b>گزارش امروز — {escape(class_group.name)}</b>"]
        for r in reports:
            lines.append(f"\n• {escape(r.title or 'گزارش')}\n{escape(r.body)}")
        await target.answer("\n".join(lines))

    elif action == "week":
        entries = (
            await session.execute(
                select(TimetableEntry, Subject)
                .join(Subject, TimetableEntry.subject_id == Subject.id)
                .where(TimetableEntry.class_id == class_id)
                .order_by(TimetableEntry.weekday, TimetableEntry.period)
            )
        ).all()
        if not entries:
            await target.answer(
                f"📅 <b>{escape(class_group.name)}</b>\nبرنامه هفتگی هنوز تنظیم نشده."
            )
            return
        lines = [f"📅 <b>برنامه هفتگی — {escape(class_group.name)}</b>"]
        current = -1
        for entry, subj in entries:
            if entry.weekday != current:
                current = entry.weekday
                lines.append(f"\n<b>{WEEKDAYS[entry.weekday]}</b>")
            t = ""
            if entry.starts_at:
                t = f" {entry.starts_at.strftime('%H:%M')}"
                if entry.ends_at:
                    t += f"–{entry.ends_at.strftime('%H:%M')}"
            lines.append(f"  {entry.period}. {escape(subj.name)}{t}")
        await target.answer("\n".join(lines))

    elif action == "pending":
        if not await can_approve(session, user, class_id):
            await target.answer("اجازه دیدن درخواست‌های در انتظار را نداری.")
            return
        pending = await list_pending_records(session, class_id)
        if not pending:
            await target.answer(f"🕓 <b>{escape(class_group.name)}</b>\nموردی در انتظار نیست.")
            return
        lines = [f"🕓 <b>در انتظار تأیید — {escape(class_group.name)}</b>"]
        for label, rec in pending:
            title = getattr(rec, "title", None) or "—"
            lines.append(f"• {label} #{rec.id}: {escape(str(title))}")
        await target.answer("\n".join(lines))

    elif action == "new":
        await target.answer(
            f"ثبت در کلاس <b>{escape(class_group.name)}</b>\n"
            "از فرمان‌های زیر در <b>گروه کلاس</b> استفاده کن:\n\n"
            "/new_homework درس | عنوان | توضیح | موعد؟\n"
            "/new_exam درس | عنوان | محدوده | زمان\n"
            "/new_report متن [| عنوان]",
            reply_markup=new_record_menu(),
        )


# ── Reply keyboard buttons ──────────────────────────────────────────


@router.message(F.text.in_({"🏫 کلاس‌های من", "کلاس‌های من"}))
async def btn_classes(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await _user(session, message.from_user)
    classes = await _classes_for_user(session, user)
    if not classes:
        await message.answer("هنوز کلاسی نداری. با /join عضو شو.")
        return
    lines = ["🏫 <b>کلاس‌های تو</b>"]
    for cid, name, school in classes:
        lines.append(f"• <b>{escape(name)}</b> — {escape(school)}\n  شناسه: <code>{cid}</code>")
    await message.answer("\n".join(lines))


@router.message(F.text.in_({"📚 تکالیف", "تکالیف"}))
async def btn_homework(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await _user(session, message.from_user)
    await _ask_class(message, session, "homework", user)


@router.message(F.text.in_({"📝 امتحانات", "امتحانات"}))
async def btn_exams(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await _user(session, message.from_user)
    await _ask_class(message, session, "exams", user)


@router.message(F.text.in_({"📅 برنامه هفتگی", "برنامه هفتگی"}))
async def btn_week(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await _user(session, message.from_user)
    await _ask_class(message, session, "week", user)


@router.message(F.text.in_({"📋 گزارش امروز", "گزارش امروز"}))
async def btn_today(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await _user(session, message.from_user)
    await _ask_class(message, session, "today", user)


@router.message(F.text.in_({"🕓 در انتظار تأیید", "در انتظار تأیید"}))
async def btn_pending(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await _user(session, message.from_user)
    await _ask_class(message, session, "pending", user)


@router.message(F.text.in_({"➕ ثبت جدید", "ثبت اطلاعات جدید", "ثبت جدید"}))
async def btn_new(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    await message.answer("چه چیزی می‌خواهی ثبت کنی؟", reply_markup=new_record_menu())


@router.message(F.text.in_({"❓ راهنما", "راهنما"}))
async def btn_help(message: Message) -> None:
    from classmate.bot.handlers.common import cmd_help

    await cmd_help(message)


# ── Inline callbacks ────────────────────────────────────────────────


@router.callback_query(F.data == "ui:home")
async def cb_home(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    user = await _user(session, callback.from_user)
    _, manager, approver = await _role_flags(session, user)
    await callback.message.answer(
        "منوی اصلی:",
        reply_markup=main_menu(is_approver=approver, is_manager=manager),
    )
    await callback.answer()


@router.callback_query(F.data == "ui:cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer("لغو شد.")
    await callback.answer()


@router.callback_query(F.data.startswith("ui:new:"))
async def cb_new_type(callback: CallbackQuery, session: AsyncSession) -> None:
    kind = callback.data.split(":")[-1]
    hints = {
        "homework": (
            "📚 ثبت تکلیف — در <b>گروه کلاس</b> بفرست:\n"
            "<code>/new_homework درس | عنوان | توضیح | موعد؟</code>\n\n"
            "مثال:\n<code>/new_homework ریاضی | تمرین ۲۰ | مسائل ۱ تا ۵ | ۱۴۰۵/۰۸/۰۱</code>"
        ),
        "exam": (
            "📝 ثبت امتحان — در <b>گروه کلاس</b>:\n"
            "<code>/new_exam درس | عنوان | محدوده | زمان</code>\n\n"
            "مثال:\n<code>/new_exam فیزیک | میان‌ترم | فصل ۱–۲ | ۱۴۰۵/۰۸/۱۰ ۱۰:۰۰</code>"
        ),
        "report": (
            "📋 ثبت گزارش — در <b>گروه کلاس</b>:\n"
            "<code>/new_report متن گزارش [| عنوان]</code>"
        ),
    }
    await callback.message.answer(hints.get(kind, "نوع نامعتبر"))
    await callback.answer()


@router.callback_query(F.data.startswith("ui:class:"))
async def cb_class_picked(callback: CallbackQuery, session: AsyncSession) -> None:
    # ui:class:{action}:{class_id}
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("داده نامعتبر", show_alert=True)
        return
    _, _, action, id_s = parts
    class_id = int(id_s)
    user = await _user(session, callback.from_user)
    await _run_action(callback.message, session, action, class_id, user)
    await callback.answer()


@router.callback_query(F.data.startswith("ui:classpage:"))
async def cb_class_page(callback: CallbackQuery, session: AsyncSession) -> None:
    # ui:classpage:{action}:{page}
    parts = callback.data.split(":")
    action, page_s = parts[2], parts[3]
    page = int(page_s)
    user = await _user(session, callback.from_user)
    classes = await _classes_for_user(session, user)
    await callback.message.edit_reply_markup(
        reply_markup=class_picker(classes, action=action, page=page)
    )
    await callback.answer()
