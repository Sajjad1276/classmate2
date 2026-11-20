from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.core.config import get_settings
from classmate.services.permissions import can_approve, get_class_with_school
from classmate.services.records import approve_record, list_pending_records, reject_record
from classmate.services.users import ensure_user

router = Router(name="approvals")


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


@router.callback_query(F.data.startswith("approve:"))
async def on_approve(callback: CallbackQuery, session: AsyncSession) -> None:
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
    await callback.answer(msg, show_alert=not ok)
    if ok and callback.message:
        await callback.message.edit_text(callback.message.html_text + f"\n\n✅ {msg}")


@router.callback_query(F.data.startswith("reject:"))
async def on_reject(callback: CallbackQuery, session: AsyncSession) -> None:
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
    await callback.answer(msg, show_alert=not ok)
    if ok and callback.message:
        await callback.message.edit_text(callback.message.html_text + f"\n\n❌ {msg}")


@router.message(Command("pending"))
async def cmd_pending(message: Message, session: AsyncSession) -> None:
    user = await _user(session, message.from_user, private=message.chat.type == "private")
    arg = message.text.split(maxsplit=1)[1] if message.text and " " in message.text else None

    from classmate.services.records import get_group_context

    if message.chat.type in {"group", "supergroup"}:
        ctx = await get_group_context(session, message.chat.id)
    elif arg:
        try:
            class_id = int(arg.strip())
        except ValueError:
            await message.answer("شناسه کلاس نامعتبر")
            return
        ctx = await get_class_with_school(session, class_id)
    else:
        await message.answer("فرمت صحیح: /pending شناسه_کلاس؛ یا این فرمان را در گروه کلاس اجرا کنید.")
        return

    if ctx is None:
        await message.answer("کلاس موردنظر پیدا نشد")
        return
    class_group = ctx[0]
    if not await can_approve(session, user, class_group.id):
        await message.answer("شما اجازه مشاهده درخواست‌های در انتظار بررسی را ندارید.")
        return

    pending = await list_pending_records(session, class_group.id)
    if not pending:
        await message.answer("در حال حاضر درخواستی برای بررسی وجود ندارد.")
        return
    lines = ["🕓 <b>در انتظار تأیید</b>"]
    for label, rec in pending:
        title = getattr(rec, "title", None) or "—"
        lines.append(f"• {label} #{rec.id}: {title}")
    await message.answer("\n".join(lines))
