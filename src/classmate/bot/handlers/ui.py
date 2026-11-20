"""Button-first navigation: menus, class picker, view actions."""

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.bot.keyboards import (
    admin_menu,
    class_picker,
    education_menu,
    education_result_kb,
    main_menu,
    management_menu,
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
from classmate.services.permissions import (
    can_approve,
    get_class_with_school,
    is_class_manager,
    user_can_access_class,
)
from classmate.services.records import list_exams, list_homework, list_pending_records, list_reports
from classmate.services.users import ensure_user

router = Router(name="ui")
logger = logging.getLogger(__name__)

WEEKDAYS = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]


async def render_panel(message: Message, text: str, markup) -> None:
    """Send an actionable inline panel and close the old Reply Keyboard.

    Editing a freshly sent message is not guaranteed by every Telegram client or
    proxy. Sending the markup with the original request prevents a panel from
    appearing as plain text with no actionable options. The keyboard-removal
    update is sent as an invisible message and deleted shortly after Telegram
    clients have had time to apply the keyboard removal, so it does not add
    clutter to the chat.
    """
    await message.answer(text, reply_markup=markup)
    cleanup_message = await message.answer("\u2063", reply_markup=ReplyKeyboardRemove())
    try:
        await asyncio.sleep(0.35)
        await cleanup_message.delete()
    except Exception:
        logger.debug("Could not delete invisible keyboard cleanup message", exc_info=True)


async def delete_panel(message: Message) -> None:
    """Delete the current inline panel before opening its next screen."""
    try:
        await message.delete()
    except Exception:
        logger.debug("Could not delete previous inline panel", exc_info=True)


async def get_user(session: AsyncSession, tg, private: bool = False) -> User:
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
    if not user.is_active:
        return False, False, False
    if user.is_platform_admin:
        return True, True, True

    school_admin = (
        await session.scalar(
            select(SchoolMembership.id)
            .join(School, SchoolMembership.school_id == School.id)
            .where(
                SchoolMembership.user_id == user.id,
                SchoolMembership.role == Role.SCHOOL_ADMIN,
                SchoolMembership.is_active.is_(True),
                School.is_active.is_(True),
            )
        )
    ) is not None
    roles = set(
        (
            await session.scalars(
                select(ClassMembership.role)
                .join(ClassGroup, ClassMembership.class_id == ClassGroup.id)
                .join(AcademicYear, ClassGroup.academic_year_id == AcademicYear.id)
                .join(School, AcademicYear.school_id == School.id)
                .where(
                    ClassMembership.user_id == user.id,
                    ClassMembership.is_active.is_(True),
                    ClassGroup.is_active.is_(True),
                    School.is_active.is_(True),
                )
            )
        ).all()
    )
    manager = Role.CLASS_MANAGER in roles or school_admin
    approver = school_admin or manager or Role.TRUSTED_APPROVER in roles
    return school_admin, manager, approver


async def classes_for_user(session: AsyncSession, user: User) -> list[tuple[int, str, str]]:
    if not user.is_active:
        return []
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
    return [(item.id, item.name, school.name) for item, school in (await session.execute(query)).all()]


async def menu_for(session: AsyncSession, user: User):
    admin, manager, approver = await role_flags(session, user)
    return main_menu(is_approver=approver, is_manager=manager, is_admin=admin or user.is_platform_admin)


async def ask_class(message: Message, session: AsyncSession, action: str, user: User) -> None:
    classes = await classes_for_user(session, user)
    if not classes:
        await message.answer(
            "هنوز عضو کلاسی نیستید.\nاز «🔑 ورود با کد دعوت» استفاده کنید یا کد دعوت را از مدیر کلاس دریافت کنید."
        )
        return
    if len(classes) == 1:
        await run_view(message, session, action, classes[0][0], user)
        return
    await render_panel(message, "🏫 کلاس موردنظر را انتخاب کنید:", class_picker(classes, action=action))


async def _education_result(target: Message, text: str, close_reply_keyboard: bool = True) -> None:
    if close_reply_keyboard:
        await render_panel(target, text, education_result_kb())
    else:
        await target.answer(text, reply_markup=education_result_kb())


async def run_view(
    target: Message,
    session: AsyncSession,
    action: str,
    class_id: int,
    user: User,
    *,
    close_reply_keyboard: bool = True,
) -> None:
    if not await user_can_access_class(session, user, class_id):
        await _education_result(target, "دسترسی شما به این کلاس فعال نیست.", close_reply_keyboard)
        return
    ctx = await get_class_with_school(session, class_id)
    if ctx is None:
        await _education_result(target, "کلاس موردنظر پیدا نشد. لطفاً دوباره تلاش کنید.", close_reply_keyboard)
        return
    class_group, _, school = ctx
    local_today = datetime.now(ZoneInfo(school.timezone)).date()

    if action == "homework":
        rows = await list_homework(session, class_id)
        if not rows:
            await _education_result(target, f"📚 <b>{escape(class_group.name)}</b>\nتکلیف تأییدشده‌ای نیست.", close_reply_keyboard)
            return
        lines = [f"📚 <b>تکالیف — {escape(class_group.name)}</b>"]
        for hw, subj in rows:
            lines.append(
                f"\n• <b>{escape(subj.name)}</b> — {escape(hw.title)}\n"
                f"  {escape(hw.description)}\n  موعد: {jalali_date(hw.due_on)}"
            )
        await _education_result(target, "\n".join(lines), close_reply_keyboard)

    elif action == "exams":
        rows = await list_exams(session, class_id)
        if not rows:
            await _education_result(target, f"📝 <b>{escape(class_group.name)}</b>\nامتحان آینده‌ای نیست.", close_reply_keyboard)
            return
        lines = [f"📝 <b>امتحانات — {escape(class_group.name)}</b>"]
        for exam, subj in rows:
            lines.append(
                f"\n• <b>{escape(subj.name)}</b> — {escape(exam.title)}\n"
                f"  {escape(exam.scope or '—')}\n"
                f"  {jalali_datetime(exam.scheduled_at, school.timezone)}"
            )
        await _education_result(target, "\n".join(lines), close_reply_keyboard)

    elif action == "today":
        reports = await list_reports(session, class_id, local_today)
        if not reports:
            await _education_result(
                target,
                f"📋 <b>{escape(class_group.name)}</b>\n"
                f"گزارشی برای امروز ({jalali_date(local_today)}) نیست.",
                close_reply_keyboard,
            )
            return
        lines = [f"📋 <b>گزارش‌های امروز — {escape(class_group.name)}</b>"]
        for r in reports:
            lines.append(f"\n• {escape(r.title or 'گزارش')}\n{escape(r.body)}")
        await _education_result(target, "\n".join(lines), close_reply_keyboard)

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
            await _education_result(target, f"📅 <b>{escape(class_group.name)}</b>\nبرنامه تنظیم نشده.", close_reply_keyboard)
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
        await _education_result(target, "\n".join(lines), close_reply_keyboard)

    elif action == "pending":
        if not await can_approve(session, user, class_id):
            await _education_result(target, "شما اجازه مشاهده این بخش را ندارید.", close_reply_keyboard)
            return
        pending = await list_pending_records(session, class_id)
        if not pending:
            await _education_result(target, f"🕓 <b>{escape(class_group.name)}</b>\nموردی در انتظار نیست.", close_reply_keyboard)
            return
        lines = [f"🕓 <b>در انتظار — {escape(class_group.name)}</b>"]
        for label, rec in pending:
            title = getattr(rec, "title", None) or "—"
            lines.append(f"• {label} #{rec.id}: {escape(str(title))}")
        await _education_result(target, "\n".join(lines), close_reply_keyboard)


# ── Reply buttons ───────────────────────────────────────────────────


@router.message(F.text == "❌ انصراف")
async def btn_cancel(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    await message.answer("عملیات لغو شد.", reply_markup=await menu_for(session, user))


@router.message(F.text.in_({"🏫 کلاس‌های من", "کلاس‌های من"}))
async def btn_classes(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    classes = await classes_for_user(session, user)
    if not classes:
        await message.answer("هنوز عضو کلاسی نیستید. از «🔑 ورود با کد دعوت» استفاده کنید.")
        return
    lines = ["🏫 <b>کلاس‌های شما</b>"]
    for cid, name, school in classes:
        lines.append(f"• <b>{escape(name)}</b> — {escape(school)}")
    await message.answer("\n".join(lines))


@router.message(F.text.in_({"📚 تکالیف من", "تکالیف"}))
async def btn_hw(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    await ask_class(message, session, "homework", user)


@router.message(F.text.in_({"📝 تقویم امتحانات", "امتحانات"}))
async def btn_ex(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    await ask_class(message, session, "exams", user)


@router.message(F.text.in_({"📅 برنامه هفتگی", "برنامه هفتگی", "برنامه"}))
async def btn_week(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    await ask_class(message, session, "week", user)


@router.message(F.text.in_({"📋 گزارش‌های امروز", "گزارش‌های امروز", "گزارش امروز"}))
async def btn_today(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    await ask_class(message, session, "today", user)


@router.message(F.text.in_({"🕓 موارد در انتظار بررسی", "در انتظار تأیید", "موارد در انتظار بررسی"}))
async def btn_pending(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    await ask_class(message, session, "pending", user)


@router.message(F.text.in_({"🎓 بخش آموزشی", "بخش آموزشی"}))
async def btn_education(message: Message) -> None:
    if message.chat.type != "private":
        return
    await render_panel(message, "🎓 <b>بخش آموزشی</b>\nگزینه موردنظر را انتخاب کنید:", education_menu())


@router.message(F.text.in_({"➕ ثبت اطلاعات", "ثبت اطلاعات", "ثبت جدید"}))
async def btn_new(message: Message) -> None:
    if message.chat.type != "private":
        return
    await render_panel(
        message,
        "📝 <b>ثبت اطلاعات جدید</b>\n\nنوع اطلاعاتی را که می‌خواهید ثبت کنید انتخاب کنید:",
        new_record_menu(),
    )


@router.message(F.text.in_({"⚙️ مدیریت", "⚙️ مدیریت کلاس", "مدیریت", "مدیریت کلاس"}))
async def btn_manage(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    is_admin, is_manager, is_approver = await role_flags(session, user)
    await render_panel(
        message,
        "⚙️ <b>مدیریت</b>\n\nعملیات موردنظر را انتخاب کنید:",
        management_menu(
            is_admin=is_admin or user.is_platform_admin,
            is_manager=is_manager,
            is_approver=is_approver,
        ),
    )


@router.message(F.text.in_({"🏛 مدیریت سامانه", "مدیریت سامانه"}))
async def btn_admin(message: Message) -> None:
    if message.chat.type != "private":
        return
    await render_panel(
        message,
        "🏛 <b>مدیریت سامانه</b>\n\n"
        "عملیات موردنظر را انتخاب کنید:",
        admin_menu(),
    )


@router.message(F.text.in_({"🔑 ورود با کد دعوت", "عضویت با کد"}))
async def btn_join(message: Message, state: FSMContext) -> None:
    if message.chat.type != "private":
        return
    await state.set_state(JoinForm.code)
    from classmate.bot.keyboards import cancel_kb

    await message.answer("🔑 کد دعوت کلاس را ارسال کنید:", reply_markup=cancel_kb())


@router.message(F.text.in_({"❓ راهنمای استفاده", "راهنما"}))
async def btn_help(message: Message, session: AsyncSession) -> None:
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    await message.answer(
        "<b>راهنمای کوتاه ClassMate 2</b>\n\n"
        "همه امکانات از دکمه‌های پایین و پنل‌های اینلاین در دسترس است.\n\n"
        "<b>بخش‌های عمومی</b>\n"
        "• <b>🏠 داشبورد من</b> — خلاصه تکالیف نزدیک، امتحانات و اطلاعیه‌ها\n"
        "• <b>🏫 کلاس‌های من</b> — مشاهده کلاس‌های فعال شما\n"
        "• <b>🔎 جست‌وجو</b> — جست‌وجو در تکلیف، امتحان و گزارش\n"
        "• <b>🎓 بخش آموزشی</b> — تکلیف، امتحان، برنامه، نمره، حضور و نظرسنجی\n"
        "• <b>📣 اطلاعیه‌ها</b> — مشاهده اطلاعیه‌های کلاس\n"
        "• <b>🔔 اعلان‌ها</b> — مدیریت یادآوری‌ها\n\n"
        "<b>امکانات آموزشی دانش‌آموز</b>\n"
        "• <b>📤 ارسال تکلیف</b> — ارسال پاسخ به‌صورت متن، فایل یا تصویر\n"
        "• <b>📊 نمرات من</b> — مشاهده نمرات، بازخورد و میانگین تقریبی\n"
        "• <b>🟢 حضور من</b> — مشاهده وضعیت حضور، غیبت، تأخیر و موارد موجه\n"
        "• <b>🗳 نظرسنجی‌ها</b> — شرکت در نظرسنجی‌ها و آزمون‌های کوتاه\n\n"
        "<b>امکانات مدیر کلاس</b>\n"
        "• <b>🗂 مدیریت محتوا</b> — ویرایش یا آرشیو تکلیف، امتحان و گزارش\n"
        "• <b>📝 بررسی پاسخ‌ها</b> — مشاهده پاسخ‌ها و ثبت بازخورد برای دانش‌آموز\n"
        "• <b>👨‍🏫 ارزیابی کلاس</b> — ثبت نمره، حضور‌وغیاب و ایجاد نظرسنجی\n"
        "• <b>مدیریت کلاس</b> — مدیریت کدها، درس‌ها و برنامه هفتگی\n\n"
        "<b>اتصال و پشتیبانی</b>\n"
        "• برای اتصال گروه، کد اتصال را در گروه کلاس ارسال کنید.\n"
        "• برای عضویت، «🔑 ورود با کد دعوت» را انتخاب کنید.\n"
        "• پشتیبانی و ارتباط با سازنده: <b>@Mizanup</b>\n\n"
        "برای لغو هر فرم، گزینه «❌ انصراف» را انتخاب کنید.",
        reply_markup=await menu_for(session, user),
    )


# ── Inline: category menus / home / pick class ─────────────────────


@router.callback_query(F.data.startswith("menu:"))
async def cb_category(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    action = callback.data.split(":", 1)[1]
    await delete_panel(callback.message)
    user = await get_user(session, callback.from_user, private=bool(callback.message and callback.message.chat.type == "private"))
    if not user.is_active:
        await callback.answer("حساب کاربری شما غیرفعال است.", show_alert=True)
        return
    if action in {"homework", "exams", "week", "today", "content", "reviews", "evaluation", "pending"}:
        mapped = {"reviews": "review_submissions", "content": "content"}.get(action, action)
        classes = await classes_for_user(session, user)
        if len(classes) == 1 and mapped in {"content", "review_submissions", "evaluation"}:
            cid = classes[0][0]
            if mapped == "content":
                from classmate.bot.handlers.phase1 import _render_content
                await _render_content(callback.message, session, cid)
            elif mapped == "review_submissions":
                from classmate.bot.handlers.phase2 import submissions_panel
                await submissions_panel(callback.message, session, cid)
            else:
                from classmate.bot.handlers.phase2 import evaluation_panel
                await evaluation_panel(callback.message, session, state, cid)
        else:
            await ask_class(callback.message, session, mapped, user)
    elif action == "class_manage":
        _school_admin, manager, _approver = await role_flags(session, user)
        if not manager:
            await callback.answer("دسترسی مدیریت کلاس ندارید.", show_alert=True)
            return
        await render_panel(
            callback.message,
            "⚙️ <b>مدیریت کلاس</b>\nعملیات موردنظر را انتخاب کنید:",
            manage_class_menu(),
        )
    elif action == "admin":
        if not user.is_platform_admin:
            await callback.answer("دسترسی مدیر سامانه لازم است.", show_alert=True)
            return
        await render_panel(
            callback.message,
            "🏛 <b>مدیریت سامانه</b>\nعملیات موردنظر را انتخاب کنید:",
            admin_menu(),
        )
    elif action == "education":
        await render_panel(callback.message, "🎓 <b>بخش آموزشی</b>\nگزینه موردنظر را انتخاب کنید:", education_menu())
    elif action == "new":
        await render_panel(
            callback.message,
            "📝 <b>ثبت اطلاعات</b>\nنوع اطلاعات موردنظر را انتخاب کنید:",
            new_record_menu(),
        )
    else:
        from classmate.bot.handlers.phase2 import my_attendance, my_grades, polls, submit_entry
        handlers = {"submit": submit_entry, "grades": my_grades, "attendance": my_attendance, "polls": polls}
        handler = handlers.get(action)
        if handler:
            await handler(callback.message, session)
    await callback.answer()


@router.callback_query(F.data == "ui:home")
async def cb_home(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await delete_panel(callback.message)
    user = await get_user(session, callback.from_user, private=bool(callback.message and callback.message.chat.type == "private"))
    await callback.message.answer(
        "🏠 <b>منوی اصلی</b>\n\n"
        "گزینه موردنظر خود را انتخاب کنید:",
        reply_markup=await menu_for(session, user),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pick:"))
async def cb_pick(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    _, action, id_s = callback.data.split(":", 2)
    class_id = int(id_s)
    user = await get_user(session, callback.from_user, private=bool(callback.message and callback.message.chat.type == "private"))
    await delete_panel(callback.message)
    if not await user_can_access_class(session, user, class_id):
        await callback.answer("دسترسی شما به این کلاس فعال نیست.", show_alert=True)
        return

    manager_actions = {
        "content", "announcement", "evaluation", "review_submissions", "submissions",
        "hw", "exam", "report", "codes", "subject", "timetable", "group",
    }
    if action in manager_actions and not await is_class_manager(session, user, class_id):
        await callback.answer("این عملیات فقط برای مدیر کلاس مجاز است.", show_alert=True)
        return
    if action == "pending" and not await can_approve(session, user, class_id):
        await callback.answer("شما اجازه مشاهده موارد در انتظار را ندارید.", show_alert=True)
        return

    # View actions
    if action in {"homework", "exams", "today", "week", "pending"}:
        await run_view(callback.message, session, action, class_id, user)
        await callback.answer()
        return

    if action == "calendar":
        from classmate.bot.handlers.phase1 import _render_calendar

        await _render_calendar(callback.message, session, class_id, next(
            name for cid, name, _school in await classes_for_user(session, user) if cid == class_id
        ))
        await callback.answer()
        return

    if action == "prefs":
        from classmate.bot.handlers.phase1 import _render_preferences

        class_name = next(name for cid, name, _school in await classes_for_user(session, user) if cid == class_id)
        await _render_preferences(callback.message, session, user, class_id, class_name)
        await callback.answer()
        return

    if action == "announcement":
        from classmate.bot.handlers.phase1 import start_announcement_after_class

        await start_announcement_after_class(callback.message, state, class_id)
        await callback.answer()
        return

    if action == "content":
        from classmate.bot.handlers.phase1 import _render_content

        await _render_content(callback.message, session, class_id)
        await callback.answer()
        return

    if action == "submissions":
        from classmate.bot.handlers.phase2 import _show_homeworks

        await _show_homeworks(callback.message, session, class_id)
        await callback.answer()
        return

    if action == "evaluation":
        from classmate.bot.handlers.phase2 import evaluation_panel

        await evaluation_panel(callback.message, session, state, class_id)
        await callback.answer()
        return

    if action == "review_submissions":
        from classmate.bot.handlers.phase2 import submissions_panel

        await submissions_panel(callback.message, session, class_id)
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
    elif action == "group":
        from classmate.bot.handlers.wizards import start_group_after_class

        await start_group_after_class(callback.message, state, class_id)


@router.callback_query(F.data.startswith("pickpage:"))
async def cb_pickpage(callback: CallbackQuery, session: AsyncSession) -> None:
    _, action, page_s = callback.data.split(":", 2)
    user = await get_user(session, callback.from_user, private=bool(callback.message and callback.message.chat.type == "private"))
    classes = await classes_for_user(session, user)
    await callback.message.edit_reply_markup(
        reply_markup=class_picker(classes, action=action, page=int(page_s))
    )
    await callback.answer()
