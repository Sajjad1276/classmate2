"""Owner-only broadcast composer backed by the durable Telegram outbox."""

from datetime import datetime, timezone
from html import escape
from uuid import uuid4
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.bot.handlers.ui import get_user
from classmate.bot.keyboards import cancel_kb, main_menu
from classmate.bot.states import BroadcastForm
from classmate.core.config import get_settings
from classmate.core.enums import OutboxStatus
from classmate.db.models import TelegramOutbox, User
from classmate.domain.dates import jalali_date

router = Router(name="broadcast")
TEHRAN = ZoneInfo("Asia/Tehran")
INSERT_BATCH_SIZE = 200

PROMPT = (
    "👈 لطفا متن برای ارسال همگانی رو ارسال کنید.\n\n"
    "میتوانید از واژگان زیر جهت فرمت متن خود استفاده کنید:\n\n"
    "<code>{current_date}</code> — نمایش تاریخ\n"
    "<code>{user_name}</code> — نمایش نام کاربر\n"
    "<code>{user_mention}</code> — نمایش لینک به پروفایل کاربر با نام کاربر\n"
    "<code>{user_username}</code> — نمایش یوزرنیم کاربر\n"
    "<code>{user_id}</code> — نمایش آیدی عددی کاربر\n\n"
    "<code>{bot_name}</code> — نمایش نام ربات\n"
    "<code>{bot_username}</code> — نمایش یوزرنیم ربات\n\n"
    "برای لغو، دکمه «❌ انصراف» را بزنید."
)


def render_broadcast_template(
    template: str,
    *,
    user: User,
    bot_name: str,
    bot_username: str,
    current_date: str,
) -> str:
    """Substitute recipient-specific values while keeping HTML safe."""
    username = f"@{user.username}" if user.username else "ندارد"
    values = {
        "{current_date}": escape(current_date),
        "{user_name}": escape(user.full_name or ""),
        "{user_mention}": (
            f'<a href="tg://user?id={user.telegram_id}">'
            f"{escape(user.full_name or 'کاربر')}</a>"
        ),
        "{user_username}": escape(username),
        "{user_id}": str(user.telegram_id),
        "{bot_name}": escape(bot_name),
        "{bot_username}": escape(bot_username),
    }
    rendered = template
    for placeholder, value in values.items():
        rendered = rendered.replace(placeholder, value)
    return rendered


async def _owner_user(session: AsyncSession, telegram_user) -> User | None:
    try:
        user = await get_user(session, telegram_user, private=True)
    except PermissionError:
        return None
    return user if user.is_active and user.is_platform_admin else None


@router.message(F.text.in_({"⚙️ مدیریت", "🔴 مدیریت", "مدیریت"}))
async def open_management_panel(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    if message.chat.type != "private":
        return
    owner = await _owner_user(session, message.from_user)
    if owner is None:
        await message.answer("این بخش فقط برای مالک سامانه فعال است.")
        return
    await state.clear()
    await message.answer(
        "⚙️ <b>مدیریت سامانه</b>\n\n"
        "برای ارسال پیام به اعضای واجد شرایط ربات، گزینه زیر را انتخاب کنید.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="📣 ارسال پیام همگانی",
                callback_data="broadcast:start",
                style="danger",
            )
        ]]),
    )


@router.callback_query(F.data == "broadcast:start")
async def start_broadcast(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer("این بخش فقط در گفت‌وگوی خصوصی در دسترس است.", show_alert=True)
        return
    owner = await _owner_user(session, callback.from_user)
    if owner is None:
        await callback.answer("دسترسی مالک سامانه لازم است.", show_alert=True)
        return

    await state.clear()
    await state.set_state(BroadcastForm.content)
    await callback.answer()
    await callback.message.answer(PROMPT, reply_markup=cancel_kb())


@router.message(BroadcastForm.content, F.text == "❌ انصراف", ~CommandStart())
async def cancel_broadcast_draft(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    await state.clear()
    owner = await _owner_user(session, message.from_user)
    await message.answer(
        "ارسال پیام همگانی لغو شد.",
        reply_markup=main_menu(is_owner=owner is not None),
    )


@router.message(BroadcastForm.content, F.text, ~CommandStart())
async def receive_broadcast_content(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if message.chat.type != "private":
        await state.clear()
        return
    owner = await _owner_user(session, message.from_user)
    if owner is None:
        await state.clear()
        await message.answer("دسترسی مالک سامانه لازم است.")
        return

    raw_text = (message.text or "").strip()
    if not raw_text:
        await message.answer("متن پیام خالی است؛ متن موردنظر را ارسال کنید.")
        return

    template = message.html_text or escape(message.text or "")
    bot_user = await message.bot.get_me()
    today = jalali_date(datetime.now(TEHRAN).date())
    preview = render_broadcast_template(
        template,
        user=owner,
        bot_name=bot_user.first_name or "ClassMate",
        bot_username=f"@{bot_user.username}" if bot_user.username else "",
        current_date=today,
    )
    if len(preview) > 4096:
        await message.answer(
            "متن پس از جایگزینی متغیرها از محدودیت ۴۰۹۶ کاراکتر تلگرام بیشتر می‌شود. "
            "لطفاً متن را کوتاه‌تر کنید."
        )
        return

    audience_count = await session.scalar(
        select(func.count(User.id)).where(
            User.is_active.is_(True),
            User.private_chat_started.is_(True),
        )
    ) or 0
    if audience_count == 0:
        await state.clear()
        await message.answer(
            "در حال حاضر هیچ کاربر فعالی که گفت‌وگوی خصوصی ربات را شروع کرده باشد وجود ندارد.",
            reply_markup=main_menu(is_owner=True),
        )
        return

    campaign_id = uuid4().hex
    await state.update_data(
        broadcast_template=template,
        broadcast_campaign_id=campaign_id,
        broadcast_audience_count=audience_count,
    )
    await state.set_state(BroadcastForm.confirm)

    await message.answer("پیش‌نمایش برای حساب مالک:", reply_markup=ReplyKeyboardRemove())
    await message.answer(preview)
    await message.answer(
        f"👥 تعداد مخاطبان واجد شرایط: <b>{audience_count}</b>\n"
        "متغیرهای شخصی‌سازی‌شده برای هر مخاطب جداگانه جایگزین می‌شوند.\n\n"
        "ارسال هنوز شروع نشده است. برای قرار دادن پیام‌ها در صف ارسال، تأیید کنید.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="✅ تأیید و ارسال همگانی",
                callback_data="broadcast:confirm",
                style="success",
            )],
            [InlineKeyboardButton(
                text="❌ لغو ارسال",
                callback_data="broadcast:cancel",
                style="danger",
            )],
        ]),
    )


@router.message(BroadcastForm.content)
async def reject_non_text_broadcast_content(message: Message) -> None:
    await message.answer("لطفاً متن پیام را به‌صورت پیام متنی ارسال کنید، نه فایل یا رسانه.")


@router.callback_query(F.data == "broadcast:cancel")
async def cancel_broadcast_confirmation(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer("درخواست معتبر نیست.", show_alert=True)
        return
    owner = await _owner_user(session, callback.from_user)
    if owner is None:
        await callback.answer("دسترسی مالک سامانه لازم است.", show_alert=True)
        await state.clear()
        return
    await state.clear()
    await callback.answer("ارسال لغو شد.")
    await callback.message.edit_text("❌ ارسال پیام همگانی لغو شد.")
    await callback.message.answer(
        "به پنل اصلی بازگشتید.",
        reply_markup=main_menu(is_owner=True),
    )


@router.callback_query(F.data == "broadcast:confirm")
async def confirm_broadcast(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer("درخواست معتبر نیست.", show_alert=True)
        return
    owner = await _owner_user(session, callback.from_user)
    if owner is None:
        await callback.answer("دسترسی مالک سامانه لازم است.", show_alert=True)
        await state.clear()
        return

    data = await state.get_data()
    template = data.get("broadcast_template")
    campaign_id = data.get("broadcast_campaign_id")
    if not isinstance(template, str) or not campaign_id:
        await callback.answer("پیش‌نویس پیدا نشد یا منقضی شده است.", show_alert=True)
        await state.clear()
        return

    bot_user = await callback.bot.get_me()
    today = jalali_date(datetime.now(TEHRAN).date())
    recipients = list((await session.scalars(
        select(User)
        .where(
            User.is_active.is_(True),
            User.private_chat_started.is_(True),
        )
        .order_by(User.id)
    )).all())
    if not recipients:
        await state.clear()
        await callback.answer("مخاطب فعالی وجود ندارد.", show_alert=True)
        await callback.message.edit_text("هیچ مخاطب فعالی برای ارسال پیدا نشد.")
        return

    scheduled_at = datetime.now(timezone.utc)
    rows: list[dict] = []
    skipped_too_long = 0
    for recipient in recipients:
        body = render_broadcast_template(
            template,
            user=recipient,
            bot_name=bot_user.first_name or "ClassMate",
            bot_username=f"@{bot_user.username}" if bot_user.username else "",
            current_date=today,
        )
        if len(body) > 4096:
            skipped_too_long += 1
            continue
        rows.append({
            "target_type": "private",
            "target_chat_id": recipient.telegram_id,
            "class_id": None,
            "entity_type": None,
            "entity_id": None,
            "expected_record_status": None,
            "recipient_user_id": recipient.id,
            "text": body,
            "parse_mode": "HTML",
            "disable_notification": False,
            "action_type": None,
            "action_id": None,
            "scheduled_at": scheduled_at,
            "status": OutboxStatus.PENDING,
            "attempts": 0,
            "last_error": None,
            "locked_at": None,
            "sent_at": None,
            "idempotency_key": f"broadcast:{campaign_id}:{recipient.id}",
        })

    queued = 0
    db_url = get_settings().database_url
    dialect = "postgresql" if "postgresql" in db_url else "sqlite" if "sqlite" in db_url else "other"
    for offset in range(0, len(rows), INSERT_BATCH_SIZE):
        chunk = rows[offset:offset + INSERT_BATCH_SIZE]
        if dialect == "postgresql":
            statement = pg_insert(TelegramOutbox).values(chunk).on_conflict_do_nothing(
                index_elements=[TelegramOutbox.idempotency_key]
            )
            result = await session.execute(statement)
            queued += max(result.rowcount or 0, 0)
        elif dialect == "sqlite":
            statement = sqlite_insert(TelegramOutbox).values(chunk).on_conflict_do_nothing(
                index_elements=[TelegramOutbox.idempotency_key]
            )
            result = await session.execute(statement)
            queued += max(result.rowcount or 0, 0)
        else:
            session.add_all(TelegramOutbox(**row) for row in chunk)
            queued += len(chunk)

    if not rows:
        await state.clear()
        await callback.answer("متنی با طول مجاز برای ارسال پیدا نشد.", show_alert=True)
        await callback.message.edit_text(
            f"هیچ پیامی در صف قرار نگرفت. تمام {skipped_too_long} مخاطب به دلیل طول بیش از حد متن رد شدند."
        )
        await callback.message.answer("به پنل اصلی بازگشتید.", reply_markup=main_menu(is_owner=True))
        return

    await state.clear()
    await callback.answer("پیام‌ها در صف ارسال ثبت شدند.")
    await callback.message.edit_text(
        "✅ <b>درخواست ارسال همگانی ثبت شد</b>\n\n"
        f"📨 پیام‌های قرارگرفته در صف: <b>{queued}</b>\n"
        f"⏭ ردشده به‌دلیل طول بیش از حد: <b>{skipped_too_long}</b>\n\n"
        "ارسال توسط Worker انجام می‌شود. کاربرانی که ربات را مسدود کرده باشند هنگام تحویل از صف کنار گذاشته می‌شوند.",
    )
    await callback.message.answer(
        "به پنل اصلی بازگشتید.",
        reply_markup=main_menu(is_owner=True),
    )
