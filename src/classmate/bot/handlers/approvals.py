from html import escape
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.core.config import get_settings
from classmate.core.enums import RecordStatus
from classmate.domain.dates import jalali_date, jalali_datetime
from classmate.db.models import DailyReport, Exam, Homework, Subject, User
from classmate.services.permissions import can_approve, get_class_with_school
from classmate.services.records import approve_record, reject_record
from classmate.bot.handlers.ui import delete_panel, render_pending_for_class, show_pending_for_user
from classmate.services.users import ensure_user

router = Router(name="approvals")


def _escape_limited(value: str, limit: int = 1800) -> str:
    parts: list[str] = []
    used = 0
    for character in value:
        escaped = escape(character)
        if used + len(escaped) > limit:
            return "".join(parts) + "…"
        parts.append(escaped)
        used += len(escaped)
    return "".join(parts)


async def _user(session, tg_user, private=False):
    settings = get_settings()
    user = await ensure_user(
        session,
        telegram_id=tg_user.id,
        username=tg_user.username,
        full_name=tg_user.full_name or "User",
        private_chat_started=private,
    )
    if tg_user.id in settings.bootstrap_admin_id_set:
        user.is_platform_admin = True
    return user




@router.callback_query(F.data.startswith("review:open:"))
async def open_review_item(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer("بررسی درخواست فقط در پنل خصوصی ربات انجام می‌شود.", show_alert=True)
        return
    try:
        _, _, entity_type, record_id_s, class_id_s = callback.data.split(":", 4)
        record_id, class_id = int(record_id_s), int(class_id_s)
    except (ValueError, TypeError):
        await callback.answer("درخواست انتخاب‌شده معتبر نیست.", show_alert=True)
        return

    model = {
        "homework": Homework,
        "exam": Exam,
        "report": DailyReport,
    }.get(entity_type)
    if model is None:
        await callback.answer("نوع درخواست معتبر نیست.", show_alert=True)
        return
    user = await _user(session, callback.from_user, private=True)
    if not await can_approve(session, user, class_id):
        await callback.answer("دسترسی بررسی این کلاس را ندارید.", show_alert=True)
        return
    record = await session.get(model, record_id)
    if record is None or record.class_id != class_id or record.status != RecordStatus.PENDING:
        await callback.answer("این درخواست دیگر در صف بررسی نیست.", show_alert=True)
        await delete_panel(callback.message)
        await render_pending_for_class(callback.message, session, user, class_id)
        return

    context = await get_class_with_school(session, class_id)
    if context is None:
        await callback.answer("کلاس دیگر فعال نیست.", show_alert=True)
        return
    class_group, _, school = context
    creator = await session.get(User, record.created_by)
    creator_name = creator.full_name if creator is not None else "کاربر نامشخص"
    lines = [
        f"🕓 <b>بررسی { {'homework': 'تکلیف', 'exam': 'امتحان', 'report': 'گزارش روزانه'}[entity_type] }</b>",
        f"کلاس: <b>{escape(class_group.name)}</b>",
        f"ثبت‌کننده: <b>{escape(creator_name)}</b>",
        f"زمان ثبت: {jalali_datetime(record.created_at, school.timezone)}",
    ]
    if entity_type == "homework":
        subject = await session.get(Subject, record.subject_id)
        lines.extend(
            [
                f"درس: {escape(subject.name if subject is not None else '—')}",
                f"عنوان: <b>{escape(record.title)}</b>",
                f"توضیحات: {_escape_limited(record.description, 1800)}",
                f"موعد: {jalali_date(record.due_on) if record.due_on else 'بدون موعد'}",
            ]
        )
    elif entity_type == "exam":
        subject = await session.get(Subject, record.subject_id)
        lines.extend(
            [
                f"درس: {escape(subject.name if subject is not None else '—')}",
                f"عنوان: <b>{escape(record.title)}</b>",
                f"محدوده: {_escape_limited(record.scope or '—', 1500)}",
                f"زمان برگزاری: {jalali_datetime(record.scheduled_at, school.timezone)}",
            ]
        )
    else:
        lines.extend(
            [
                f"تاریخ گزارش: {jalali_date(record.report_date)}",
                f"عنوان: <b>{escape((record.title or 'گزارش روزانه')[:180])}</b>",
                "",
                _escape_limited(record.body, 1800),
            ]
        )

    if record.created_by == user.id:
        lines.extend(["", "⚠️ این درخواست را خودتان ثبت کرده‌اید؛ برای جلوگیری از تأیید خودکار، اپراتور دیگری باید آن را بررسی کند."])
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="↩️ بازگشت به صف", callback_data=f"review:back:{class_id}")],
            ]
        )
    else:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ تأیید و انتشار", callback_data=f"review:approve:{entity_type}:{record_id}:{class_id}", style="success")],
                [InlineKeyboardButton(text="❌ رد درخواست", callback_data=f"review:reject:{entity_type}:{record_id}:{class_id}", style="danger")],
                [InlineKeyboardButton(text="↩️ بازگشت به صف", callback_data=f"review:back:{class_id}")],
            ]
        )
    await callback.message.edit_text("\n".join(lines), reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("review:approve:"))
async def approve_review_item(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer("تأیید فقط از پنل خصوصی ربات مجاز است.", show_alert=True)
        return
    try:
        _, _, entity_type, record_id_s, class_id_s = callback.data.split(":", 4)
        record_id, class_id = int(record_id_s), int(class_id_s)
    except (ValueError, TypeError):
        await callback.answer("درخواست انتخاب‌شده معتبر نیست.", show_alert=True)
        return
    user = await _user(session, callback.from_user, private=True)
    if not await can_approve(session, user, class_id):
        await callback.answer("دسترسی تأیید این کلاس را ندارید.", show_alert=True)
        return
    model = {"homework": Homework, "exam": Exam, "report": DailyReport}.get(entity_type)
    record = await session.get(model, record_id) if model is not None else None
    if record is None or record.class_id != class_id:
        await callback.answer("درخواست پیدا نشد.", show_alert=True)
        return
    context = await get_class_with_school(session, class_id)
    if context is None:
        await callback.answer("کلاس دیگر فعال نیست.", show_alert=True)
        return
    _, _, school = context
    ok, result = await approve_record(
        session, entity_type, record_id, user.id, school.id, class_id, school.timezone
    )
    if ok:
        await session.commit()
        await callback.message.edit_text(
            "✅ <b>درخواست تأیید شد</b>\n\n"
            "محتوا از صف خصوصی بررسی خارج شد و پیام عمومی آن برای گروه متصل در صف انتشار قرار گرفت.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🕓 بازگشت به موارد در انتظار بررسی", callback_data=f"review:back:{class_id}")],
                    [InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="ui:home", style="danger")],
                ]
            ),
        )
    await callback.answer(result, show_alert=not ok)


@router.callback_query(F.data.startswith("review:reject:"))
async def reject_review_item(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer("رد درخواست فقط از پنل خصوصی ربات مجاز است.", show_alert=True)
        return
    try:
        _, _, entity_type, record_id_s, class_id_s = callback.data.split(":", 4)
        record_id, class_id = int(record_id_s), int(class_id_s)
    except (ValueError, TypeError):
        await callback.answer("درخواست انتخاب‌شده معتبر نیست.", show_alert=True)
        return
    user = await _user(session, callback.from_user, private=True)
    if not await can_approve(session, user, class_id):
        await callback.answer("دسترسی رد درخواست در این کلاس را ندارید.", show_alert=True)
        return
    model = {"homework": Homework, "exam": Exam, "report": DailyReport}.get(entity_type)
    record = await session.get(model, record_id) if model is not None else None
    if record is None or record.class_id != class_id:
        await callback.answer("درخواست پیدا نشد.", show_alert=True)
        return
    context = await get_class_with_school(session, class_id)
    if context is None:
        await callback.answer("کلاس دیگر فعال نیست.", show_alert=True)
        return
    _, _, school = context
    ok, result = await reject_record(
        session, entity_type, record_id, user.id, school.id, class_id
    )
    if ok:
        await session.commit()
        await callback.message.edit_text(
            "❌ <b>درخواست رد شد</b>\n\n"
            "این مورد منتشر نشد و از صف بررسی خارج شد.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🕓 بازگشت به موارد در انتظار بررسی", callback_data=f"review:back:{class_id}")],
                    [InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="ui:home", style="danger")],
                ]
            ),
        )
    await callback.answer(result, show_alert=not ok)


@router.callback_query(F.data.startswith("review:back:"))
async def back_to_review_queue(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer("صف بررسی فقط در پنل خصوصی ربات در دسترس است.", show_alert=True)
        return
    try:
        class_id = int(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("کلاس انتخاب‌شده معتبر نیست.", show_alert=True)
        return
    user = await _user(session, callback.from_user, private=True)
    await delete_panel(callback.message)
    await render_pending_for_class(callback.message, session, user, class_id)
    await callback.answer()

@router.callback_query(F.data.startswith("approve:"))
async def on_approve(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message and callback.message.chat.type != "private":
        await callback.answer("بررسی درخواست‌ها از این پس فقط در پنل خصوصی ربات انجام می‌شود.", show_alert=True)
        return
    _, entity_type, id_s = callback.data.split(":", 2)
    record_id = int(id_s)
    user = await _user(session, callback.from_user)

    # Find class from record — simplified: require group context or pending list
    # For MVP we resolve via pending scan is heavy; store class in callback later.
    # Temporary: only work when we can find via get from models
    from classmate.db.models import DailyReport, Exam, Homework

    model = {"homework": Homework, "exam": Exam, "report": DailyReport}.get(entity_type)
    if model is None:
        await callback.answer("نوع اطلاعات معتبر نیست", show_alert=True)
        return
    record = await session.get(model, record_id)
    if record is None:
        await callback.answer("موردنظر پیدا نشد", show_alert=True)
        return

    if not await can_approve(session, user, record.class_id):
        await callback.answer("شما اجازه تأیید این درخواست را ندارید", show_alert=True)
        return

    ctx = await get_class_with_school(session, record.class_id)
    if ctx is None:
        await callback.answer("کلاس نامعتبر", show_alert=True)
        return
    _, _, school = ctx

    ok, msg = await approve_record(
        session, entity_type, record_id, user.id, school.id, record.class_id, school.timezone
    )
    if ok:
        await session.commit()
    await callback.answer(msg, show_alert=not ok)
    if ok and callback.message:
        await callback.message.edit_text(callback.message.html_text + f"\n\n✅ {msg}")


@router.callback_query(F.data.startswith("reject:"))
async def on_reject(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.message and callback.message.chat.type != "private":
        await callback.answer("رد درخواست‌ها از این پس فقط در پنل خصوصی ربات انجام می‌شود.", show_alert=True)
        return
    _, entity_type, id_s = callback.data.split(":", 2)
    record_id = int(id_s)
    user = await _user(session, callback.from_user)

    from classmate.db.models import DailyReport, Exam, Homework

    model = {"homework": Homework, "exam": Exam, "report": DailyReport}.get(entity_type)
    if model is None:
        await callback.answer("نوع اطلاعات معتبر نیست", show_alert=True)
        return
    record = await session.get(model, record_id)
    if record is None:
        await callback.answer("موردنظر پیدا نشد", show_alert=True)
        return
    if not await can_approve(session, user, record.class_id):
        await callback.answer("شما اجازه رد این درخواست را ندارید", show_alert=True)
        return

    ctx = await get_class_with_school(session, record.class_id)
    if ctx is None:
        await callback.answer("کلاس نامعتبر", show_alert=True)
        return
    _, _, school = ctx

    ok, msg = await reject_record(
        session, entity_type, record_id, user.id, school.id, record.class_id
    )
    if ok:
        await session.commit()
    await callback.answer(msg, show_alert=not ok)
    if ok and callback.message:
        await callback.message.edit_text(callback.message.html_text + f"\n\n❌ {msg}")


@router.message(Command("pending"))
async def cmd_pending(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await _user(session, message.from_user, private=True)
    await show_pending_for_user(message, session, user)
