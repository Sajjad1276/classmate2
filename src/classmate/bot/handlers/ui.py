"""Button-first navigation: menus, class picker, view actions."""

from datetime import date
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.bot.keyboards import (
    admin_menu,
    class_picker,
    main_menu,
    manage_class_menu,
    new_record_menu,
)
from classmate.bot.states import JoinForm
from classmate.core.config import get_settings
from classmate.core.enums import Role
from classmate.db.models import (
    AcademicYear,
    ClassGroup,
    ClassMembership,
    School,
    SchoolMembership,
    Subject,
    TimetableEntry,
    User,
)
from classmate.domain.dates import jalali_date, jalali_datetime
from classmate.services.permissions import can_approve, get_class_with_school
from classmate.services.records import list_exams, list_homework, list_pending_records, list_reports
from classmate.services.users import ensure_user

router = Router(name="ui")

WEEKDAYS = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]


async def get_user(session: AsyncSession, tg, private: bool = True) -> User:
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


async def role_flags(session: AsyncSession, user: User) -> tuple[bool, bool, bool]:
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
    manager = Role.CLASS_MANAGER in roles or school_admin
    approver = school_admin or manager or Role.TRUSTED_APPROVER in roles
    return school_admin, manager, approver


async def classes_for_user(session: AsyncSession, user: User) -> list[tuple[int, str, str]]:
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
    return [(c.id, c.name, s.name) for c, s in (await session.execute(query)).all()]


async def menu_for(session: AsyncSession, user: User):
    admin, manager, approver = await role_flags(session, user)
    return main_menu(is_approver=approver, is_manager=manager, is_admin=admin or user.is_platform_admin)


async def ask_class(message: Message, session: AsyncSession, action: str, user: User) -> None:
    classes = await classes_for_user(session, user)
    if not classes:
        await message.answer(
            "هنوز کلاسی نداری.\nاز «🔑 عضویت با کد» استفاده کن یا از مدیر کلاس کد بگیر."
        )
        return
    if len(classes) == 1:
        await run_view(message, session, action, classes[0][0], user)
        return
    await message.answer("کدام کلاس؟", reply_markup=class_picker(classes, action=action))


async def run_view(
    target: Message, session: AsyncSession, action: str, class_id: int, user: User
) -> None:
    ctx = await get_class_with_school(session, class_id)
    if ctx is None:
        await target.answer("کلاس پیدا نشد.")
        return
    class_group, _, school = ctx

    if action == "homework":
        rows = await list_homework(session, class_id)
        if not rows:
            await target.answer(f"📚 <b>{escape(class_group.name)}</b>\nتکلیف تأییدشده‌ای نیست.")
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
                f"گزارشی برای امروز ({jalali_date(date.today())}) نیست."
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
            await target.answer(f"📅 <b>{escape(class_group.name)}</b>\nبرنامه تنظیم نشده.")
            return
        lines = [f"📅 <b>برنامه — {escape(class_group.name)}</b>"]
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
            await target.answer("اجازه مشاهده نداری.")
            return
        pending = await list_pending_records(session, class_id)
        if not pending:
            await target.answer(f"🕓 <b>{escape(class_group.name)}</b>\nموردی در انتظار نیست.")
            return
        lines = [f"🕓 <b>در انتظار — {escape(class_group.name)}</b>"]
        for label, rec in pending:
            title = getattr(rec, "title", None) or "—"
            lines.append(f"• {label} #{rec.id}: {escape(str(title))}")
        await target.answer("\n".join(lines))


# ── Reply buttons ───────────────────────────────────────────────────


@router.message(F.text == "❌ انصراف")
async def btn_cancel(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    user = await get_user(session, message.from_user)
    await message.answer("لغو شد.", reply_markup=await menu_for(session, user))


@router.message(F.text.in_({"🏫 کلاس‌های من", "کلاس‌های من"}))
async def btn_classes(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user)
    classes = await classes_for_user(session, user)
    if not classes:
        await message.answer("کلاسی نداری. از «🔑 عضویت با کد» استفاده کن.")
        return
    lines = ["🏫 <b>کلاس‌های تو</b>"]
    for cid, name, school in classes:
        lines.append(f"• <b>{escape(name)}</b> — {escape(school)}")
    await message.answer("\n".join(lines))


@router.message(F.text.in_({"📚 تکالیف", "تکالیف"}))
async def btn_hw(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user)
    await ask_class(message, session, "homework", user)


@router.message(F.text.in_({"📝 امتحانات", "امتحانات"}))
async def btn_ex(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user)
    await ask_class(message, session, "exams", user)


@router.message(F.text.in_({"📅 برنامه هفتگی", "برنامه هفتگی"}))
async def btn_week(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user)
    await ask_class(message, session, "week", user)


@router.message(F.text.in_({"📋 گزارش امروز", "گزارش امروز"}))
async def btn_today(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user)
    await ask_class(message, session, "today", user)


@router.message(F.text.in_({"🕓 در انتظار تأیید", "در انتظار تأیید"}))
async def btn_pending(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user)
    await ask_class(message, session, "pending", user)


@router.message(F.text.in_({"➕ ثبت جدید", "ثبت جدید"}))
async def btn_new(message: Message) -> None:
    if message.chat.type != "private":
        return
    await message.answer("چه چیزی ثبت شود؟", reply_markup=new_record_menu())


@router.message(F.text == "⚙️ مدیریت کلاس")
async def btn_manage(message: Message) -> None:
    if message.chat.type != "private":
        return
    await message.answer("مدیریت کلاس:", reply_markup=manage_class_menu())


@router.message(F.text == "🏛 مدیریت سامانه")
async def btn_admin(message: Message) -> None:
    if message.chat.type != "private":
        return
    await message.answer("مدیریت سامانه:", reply_markup=admin_menu())


@router.message(F.text.in_({"🔑 عضویت با کد", "عضویت با کد"}))
async def btn_join(message: Message, state: FSMContext) -> None:
    if message.chat.type != "private":
        return
    await state.set_state(JoinForm.code)
    from classmate.bot.keyboards import cancel_kb

    await message.answer("کد عضویت کلاس را بفرست:", reply_markup=cancel_kb())


@router.message(F.text.in_({"❓ راهنما", "راهنما"}))
async def btn_help(message: Message, session: AsyncSession) -> None:
    user = await get_user(session, message.from_user)
    await message.answer(
        "<b>راهنمای ClassMate 2</b>\n\n"
        "همه‌چیز از دکمه‌های پایین و پنل‌های اینلاین انجام می‌شود.\n\n"
        "• <b>کلاس‌های من</b> — فهرست کلاس‌ها\n"
        "• <b>ثبت جدید</b> — تکلیف / امتحان / گزارش (گام‌به‌گام)\n"
        "• <b>تکالیف / امتحانات / برنامه / گزارش</b> — مشاهده\n"
        "• <b>عضویت با کد</b> — ورود به کلاس با کد دعوت\n"
        "• <b>مدیریت کلاس</b> — کد، درس، برنامه (مدیران)\n"
        "• در گروه: برای اتصال گروه، کد اتصال را همین‌جا بفرست\n\n"
        "برای انصراف از هر فرم: دکمه «❌ انصراف»",
        reply_markup=await menu_for(session, user),
    )


# ── Inline: home / pick class ───────────────────────────────────────


@router.callback_query(F.data == "ui:home")
async def cb_home(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    user = await get_user(session, callback.from_user)
    await callback.message.answer("منوی اصلی:", reply_markup=await menu_for(session, user))
    await callback.answer()


@router.callback_query(F.data.startswith("pick:"))
async def cb_pick(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    _, action, id_s = callback.data.split(":", 2)
    class_id = int(id_s)
    user = await get_user(session, callback.from_user)

    # View actions
    if action in {"homework", "exams", "today", "week", "pending"}:
        await run_view(callback.message, session, action, class_id, user)
        await callback.answer()
        return

    # Wizard entry points — store class and hand off via state flag
    await state.update_data(class_id=class_id)
    await callback.answer()

    if action == "hw":
        from classmate.bot.handlers.wizards import start_homework_after_class

        await start_homework_after_class(callback.message, state, session, class_id)
    elif action == "exam":
        from classmate.bot.handlers.wizards import start_exam_after_class

        await start_exam_after_class(callback.message, state, session, class_id)
    elif action == "report":
        from classmate.bot.handlers.wizards import start_report_after_class

        await start_report_after_class(callback.message, state, session, class_id)
    elif action == "codes":
        from classmate.bot.handlers.wizards import issue_codes

        await issue_codes(callback.message, session, user, class_id)
    elif action == "subject":
        from classmate.bot.handlers.wizards import start_subject_after_class

        await start_subject_after_class(callback.message, state, class_id)
    elif action == "timetable":
        from classmate.bot.handlers.wizards import start_timetable_after_class

        await start_timetable_after_class(callback.message, state, class_id)


@router.callback_query(F.data.startswith("pickpage:"))
async def cb_pickpage(callback: CallbackQuery, session: AsyncSession) -> None:
    _, action, page_s = callback.data.split(":", 2)
    user = await get_user(session, callback.from_user)
    classes = await classes_for_user(session, user)
    await callback.message.edit_reply_markup(
        reply_markup=class_picker(classes, action=action, page=int(page_s))
    )
    await callback.answer()
