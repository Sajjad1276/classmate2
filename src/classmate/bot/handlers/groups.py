"""Panel-driven, permission-checked Telegram group linking."""

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.bot.handlers.ui import get_user
from classmate.bot.states import GroupLinkForm
from classmate.services.permissions import (
    connect_group_by_code,
    get_class_with_school,
    is_class_manager,
    issue_class_group_link_code,
)

router = Router(name="group-link")


def _cancel_group_link_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ لغو اتصال",
                    callback_data="group:link:cancel",
                )
            ]
        ]
    )


@router.callback_query(F.data.regexp(r"^group:manage:[0-9]+$"))
async def group_manage_panel(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer("مدیریت اتصال گروه فقط از پنل خصوصی امکان‌پذیر است.", show_alert=True)
        return

    class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
    actor = await get_user(session, callback.from_user, private=True)
    context = await get_class_with_school(session, class_id)
    if context is None:
        await callback.answer("کلاس یا مدرسهٔ فعال پیدا نشد.", show_alert=True)
        return
    class_group, _, school = context
    if not await is_class_manager(session, actor, class_id):
        await callback.answer("فقط مدیر کلاس یا مدیر مدرسه می‌تواند این بخش را باز کند.", show_alert=True)
        return

    current_group = (
        f"گروه فعلی: <b>{class_group.group_chat_title or class_group.group_chat_id}</b>\n"
        f"شناسه گروه: <code>{class_group.group_chat_id}</code>\n\n"
        if class_group.group_chat_id is not None
        else "هنوز گروهی به این کلاس متصل نشده است.\n\n"
    )
    body = (
        f"🔗 <b>اتصال گروه کلاس {class_group.name}</b>\n"
        f"مدرسه: {school.name}\n"
        f"پایه: {class_group.grade}\n\n"
        f"{current_group}"
        "برای اتصال یا جایگزینی گروه، کد یک‌بارمصرف صادر کنید. "
        "کد فقط ۱۵ دقیقه اعتبار دارد."
    )
    await callback.answer()
    await callback.message.edit_text(
        body,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔑 صدور کد اتصال گروه",
                        callback_data=f"group:issue:{class_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ بازگشت به کلاس‌های من",
                        callback_data="ui:home",
                    )
                ],
            ]
        ),
    )


@router.callback_query(F.data.regexp(r"^group:issue:[0-9]+$"))
async def group_issue_code(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer("صدور کد اتصال فقط از پنل خصوصی امکان‌پذیر است.", show_alert=True)
        return

    class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
    actor = await get_user(session, callback.from_user, private=True)
    try:
        code, class_group, expires_at = await issue_class_group_link_code(
            session,
            class_id=class_id,
            actor=actor,
        )
    except (ValueError, PermissionError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.answer("کد اتصال صادر شد.")
    await callback.message.edit_text(
        f"🔐 <b>کد اتصال گروه · {class_group.name}</b>\n\n"
        f"<code>{code}</code>\n\n"
        "۱. ربات ClassMate را به گروه مقصد اضافه کنید و مدیر کنید.\n"
        "۲. در همان گروه فقط یک بار دستور /start را بفرستید.\n"
        "۳. دکمهٔ «اتصال این گروه به کلاس» را بزنید و این کد را ارسال کنید.\n\n"
        f"کد تا ساعت {expires_at.strftime('%H:%M')} اعتبار دارد و فقط یک بار قابل استفاده است.\n"
        "اتصال گروه قبلی این کلاس با اتصال جدید جایگزین می‌شود.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ بازگشت به مدیریت گروه",
                        callback_data=f"group:manage:{class_id}",
                    )
                ]
            ]
        ),
    )


@router.callback_query(F.data == "group:link:start")
async def group_link_start(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if callback.message is None or callback.message.chat.type not in {"group", "supergroup"}:
        await callback.answer("این دکمه باید داخل گروه تلگرام استفاده شود.", show_alert=True)
        return
    await state.clear()
    await state.set_state(GroupLinkForm.code)
    await callback.answer()
    await callback.message.answer(
        "کد یک‌بارمصرف اتصال کلاس را ارسال کنید.\n"
        "فقط مدیر یا مالک گروه مجاز است این کار را انجام دهد.",
        reply_markup=_cancel_group_link_keyboard(),
    )


@router.callback_query(F.data == "group:link:cancel")
async def group_link_cancel(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await state.clear()
    await callback.answer("اتصال گروه لغو شد.")
    if callback.message is not None:
        await callback.message.edit_reply_markup(reply_markup=None)


@router.message(GroupLinkForm.code, F.text)
async def link_group_code_input(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if message.chat.type not in {"group", "supergroup"} or message.from_user is None:
        await state.clear()
        await message.answer("اتصال گروه فقط داخل گروه تلگرام انجام می‌شود.")
        return

    code = (message.text or "").strip().upper()
    if not 16 <= len(code) <= 64 or any(ch.isspace() for ch in code):
        await message.answer(
            "کد اتصال معتبر نیست؛ کد را بدون فاصله دوباره ارسال کنید.",
            reply_markup=_cancel_group_link_keyboard(),
        )
        return

    try:
        bot_user = await message.bot.get_me()
        bot_member = await message.bot.get_chat_member(
            chat_id=message.chat.id,
            user_id=bot_user.id,
        )
        actor_member = await message.bot.get_chat_member(
            chat_id=message.chat.id,
            user_id=message.from_user.id,
        )
    except TelegramAPIError:
        await message.answer(
            "برای اتصال، ابتدا ربات ClassMate را در گروه مدیر کنید و دوباره تلاش کنید.",
            reply_markup=_cancel_group_link_keyboard(),
        )
        return

    actor = await get_user(session, message.from_user, private=False)
    try:
        async with session.begin_nested():
            linked_class = await connect_group_by_code(
                session,
                code=code,
                chat_id=message.chat.id,
                chat_title=message.chat.title or str(message.chat.id),
                actor=actor,
                bot_is_admin=bot_member.status in {"administrator", "creator"},
                actor_is_admin=actor_member.status in {"administrator", "creator"},
            )
    except (PermissionError, ValueError) as exc:
        await message.answer(
            f"{exc}\n\nکد صحیح را دوباره ارسال کنید یا اتصال را لغو کنید.",
            reply_markup=_cancel_group_link_keyboard(),
        )
        return

    await state.clear()
    await message.answer(
        "✅ اتصال گروه با موفقیت انجام شد.\n"
        f"کلاس: <b>{linked_class.name}</b>\n"
        "گروه قبلی این کلاس در صورت وجود جایگزین شد و کد یک‌بارمصرف دیگر قابل استفاده نیست."
    )
