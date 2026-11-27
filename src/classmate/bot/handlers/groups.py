"""Panel-driven, permission-checked Telegram group linking."""

import logging
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    ChatAdministratorRights,
    KeyboardButton,
    KeyboardButtonRequestChat,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.bot.handlers.ui import get_user, menu_for
from classmate.bot.states import GroupLinkForm
from classmate.services.permissions import (
    connect_group_by_code,
    hash_code,
    make_code,
    get_class_with_school,
    is_class_manager,
    issue_class_group_link_code,
)

router = Router(name="group-link")
logger = logging.getLogger(__name__)
LINE_BREAK = chr(10)


def _requested_group_admin_rights() -> ChatAdministratorRights:
    return ChatAdministratorRights(
        is_anonymous=False,
        can_manage_chat=True,
        can_delete_messages=False,
        can_manage_video_chats=False,
        can_restrict_members=False,
        can_promote_members=False,
        can_change_info=False,
        can_invite_users=False,
        can_post_stories=False,
        can_edit_stories=False,
        can_delete_stories=False,
        can_send_welcome_messages=False,
    )


def _cancel_group_link_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ لغو اتصال",
                    callback_data="group:link:cancel",
                    style="danger",
                )
            ]
        ]
    )


@router.callback_query(F.data.regexp(r"^group:manage:[0-9]+(?::owner)?$"))
async def group_manage_panel(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer("مدیریت گروه فقط از گفت‌وگوی خصوصی امکان‌پذیر است.", show_alert=True)
        return

    parts = (callback.data or "").split(":")
    class_id = int(parts[2])
    return_to_owner = len(parts) > 3 and parts[3] == "owner"
    actor = await get_user(session, callback.from_user, private=True)
    context = await get_class_with_school(session, class_id)
    if context is None:
        await callback.answer("کلاس یا مدرسهٔ فعال پیدا نشد.", show_alert=True)
        return
    class_group, _, school = context
    if not await is_class_manager(session, actor, class_id):
        await callback.answer("فقط مدیر کلاس یا مدیر مدرسه می‌تواند این بخش را باز کند.", show_alert=True)
        return

    if class_group.group_chat_id is not None:
        group_status = (
            f"گروه متصل: <b>{escape(class_group.group_chat_title or str(class_group.group_chat_id))}</b>\n"
            f"شناسه گروه: <code>{class_group.group_chat_id}</code>\n\n"
        )
        action_text = "🔄 تغییر گروه متصل"
    else:
        group_status = "هنوز گروهی به این کلاس متصل نشده است.\n\n"
        action_text = "📂 انتخاب گروه برای ثبت"

    body = (
        f"🛠 <b>مدیریت گروه کلاس {escape(class_group.name)}</b>\n"
        f"مدرسه: {escape(school.name)}\n"
        f"پایه: {escape(class_group.grade)}\n\n"
        f"{group_status}"
        "برای ثبت یا تغییر گروه، ربات باید در گروه عضو و مدیر باشد و شما نیز مدیر همان گروه باشید. "
        "تلگرام فقط گروه‌هایی را نمایش می‌دهد که این دسترسی‌ها را داشته باشند؛ پس از انتخاب، اتصال خودکار ثبت می‌شود."
    )
    back_callback = f"owner:class:{class_id}" if return_to_owner else "ui:classes"
    back_text = "‹ بازگشت به مشخصات کلاس" if return_to_owner else "‹ بازگشت به کلاس‌های من"

    await callback.answer()
    await callback.message.edit_text(
        body,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=action_text,
                        callback_data=f"group:select:{class_id}",
                        style="success",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=back_text,
                        callback_data=back_callback,
                        style="danger",
                    )
                ],
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


@router.message(GroupLinkForm.code, F.text, ~CommandStart())
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


@router.callback_query(F.data.regexp(r"^group:select:[0-9]+$"))
async def select_group_for_class(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer("انتخاب گروه فقط از پنل خصوصی امکان‌پذیر است.", show_alert=True)
        return
    class_id = int((callback.data or "").rsplit(":", 1)[-1])
    actor = await get_user(session, callback.from_user, private=True)
    if not await is_class_manager(session, actor, class_id):
        await callback.answer("شما مجاز به مدیریت این کلاس نیستید.", show_alert=True)
        return
    await state.clear()
    await state.set_state(GroupLinkForm.selecting)
    await state.update_data(group_class_id=class_id)
    picker = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="📂 انتخاب گروه تلگرام",
                    request_chat=KeyboardButtonRequestChat(
                        request_id=class_id,
                        chat_is_channel=False,
                        user_administrator_rights=_requested_group_admin_rights(),
                        bot_administrator_rights=_requested_group_admin_rights(),
                        bot_is_member=True,
                    ),
                )
            ],
            [KeyboardButton(text="❌ لغو انتخاب", style="danger")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await callback.answer()
    await callback.message.answer(
        LINE_BREAK.join([
            "📂 <b>انتخاب گروه کلاس</b>",
            "ابتدا ربات ClassMate را به گروه اضافه و مدیر کنید؛ سپس همین‌جا گروه را انتخاب کنید.",
            "دسترسی‌های لازم از قبل در درخواست تلگرام تنظیم شده‌اند. فقط گروه‌هایی نمایش داده می‌شوند که شما مدیر آن باشید و ربات نیز در آن مدیر باشد.",
            "بعد از انتخاب، گروه بدون کد و تأیید مرحلهٔ دوم به همین کلاس متصل می‌شود و نتیجه هم در گفت‌وگوی خصوصی و هم داخل گروه ارسال خواهد شد.",
        ]),
        reply_markup=picker,
    )

@router.message(GroupLinkForm.selecting, F.text.in_({"❌ لغو انتخاب", "❌ انصراف"}))
async def cancel_group_picker_message(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    await state.clear()
    await message.answer("انتخاب گروه لغو شد.", reply_markup=ReplyKeyboardRemove())
    if message.from_user is not None:
        actor = await get_user(session, message.from_user, private=True)
        await message.answer("\u2063", reply_markup=await menu_for(session, actor))


@router.message(GroupLinkForm.selecting, F.chat_shared)
async def group_chat_selected(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    shared = message.chat_shared
    data = await state.get_data()
    class_id = data.get("group_class_id")

    async def restore_menu(actor=None, notice: str | None = None) -> None:
        await state.clear()
        if notice:
            await message.answer(notice, reply_markup=ReplyKeyboardRemove())
        else:
            await message.answer("\u2063", reply_markup=ReplyKeyboardRemove())
        if actor is None and message.from_user is not None:
            actor = await get_user(session, message.from_user, private=True)
        if actor is not None:
            await message.answer("\u2063", reply_markup=await menu_for(session, actor))

    if message.chat.type != "private" or message.from_user is None:
        await state.clear()
        await message.answer("انتخاب گروه فقط از گفت‌وگوی خصوصی ربات انجام می‌شود.")
        return
    if not class_id or shared.request_id != int(class_id):
        await restore_menu(
            notice="درخواست انتخاب گروه معتبر نیست؛ دوباره از مدیریت همان کلاس اقدام کنید."
        )
        return

    actor = await get_user(session, message.from_user, private=True)
    if not await is_class_manager(session, actor, int(class_id)):
        await restore_menu(actor, "دسترسی مدیریت این کلاس برای حساب شما فعال نیست.")
        return

    try:
        chat = await message.bot.get_chat(shared.chat_id)
        bot_user = await message.bot.get_me()
        bot_member = await message.bot.get_chat_member(
            chat_id=shared.chat_id,
            user_id=bot_user.id,
        )
        actor_member = await message.bot.get_chat_member(
            chat_id=shared.chat_id,
            user_id=message.from_user.id,
        )

        if chat.type not in {"group", "supergroup"} or shared.chat_id >= 0:
            raise ValueError("فقط گروه یا سوپرگروه تلگرام قابل ثبت است.")
        if bot_member.status not in {"administrator", "creator"}:
            raise PermissionError("ربات باید پیش از انتخاب، در گروه مدیر شده باشد.")
        if actor_member.status not in {"administrator", "creator"}:
            raise PermissionError("فقط مدیر یا مالک همان گروه می‌تواند آن را به کلاس متصل کند.")

        class_context = await get_class_with_school(session, int(class_id))
        if class_context is None:
            raise ValueError("کلاس یا مدرسه دیگر فعال نیست.")
        title = chat.title or str(shared.chat_id)

        # Create and consume the one-time token internally: no manual copy/paste step.
        async with session.begin_nested():
            code, _, _ = await issue_class_group_link_code(
                session,
                class_id=int(class_id),
                actor=actor,
            )
            linked_class = await connect_group_by_code(
                session,
                code=code,
                chat_id=int(shared.chat_id),
                chat_title=title,
                actor=actor,
                bot_is_admin=True,
                actor_is_admin=True,
            )
            student_code = make_code(12)
            linked_class.member_join_code_hash = hash_code(student_code)
            linked_class.member_join_code_expires_at = None
        await session.commit()
    except (TelegramAPIError, PermissionError, ValueError) as exc:
        await restore_menu(
            actor,
            f"❌ ثبت گروه انجام نشد: {escape(str(exc))}\n"
            "دسترسی‌ها را بررسی کنید و از بخش مدیریت گروه دوباره تلاش کنید.",
        )
        return

    await state.clear()
    bot_username = bot_user.username
    if not bot_username:
        await message.answer(
            "گروه در سامانه ثبت شد، اما نام کاربری ربات برای ساخت لینک عضویت در دسترس نیست."
        )
        await message.answer("\u2063", reply_markup=await menu_for(session, actor))
        return

    student_url = f"https://t.me/{bot_username}?start=join_{student_code}"
    class_name = escape(linked_class.name)
    group_title = escape(title)
    actor_name = escape(actor.full_name or "مدیر گروه")
    group_notice_sent = True
    try:
        await message.bot.send_message(
            chat_id=shared.chat_id,
            text=(
                "✅ <b>ثبت گروه در ClassMate انجام شد</b>\n"
                f"🏫 کلاس: <b>{class_name}</b>\n"
                f"👥 گروه: <b>{group_title}</b>\n"
                f"👤 ثبت‌کننده: <a href=\"tg://user?id={actor.telegram_id}\">{actor_name}</a>\n\n"
                "از این پس اطلاعیه‌ها و پیام‌های آموزشی این کلاس در همین گروه ارسال می‌شود."
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🎓 ورود دانش‌آموزان به کلاس",
                            url=student_url,
                            style="success",
                        )
                    ]
                ]
            ),
        )
    except TelegramAPIError:
        group_notice_sent = False
        logger.exception(
            "Group was linked but confirmation could not be posted",
            extra={"class_id": int(class_id), "chat_id": int(shared.chat_id)},
        )

    confirmation = (
        "✅ <b>گروه با موفقیت ثبت شد</b>\n"
        f"🏫 کلاس: <b>{class_name}</b>\n"
        f"👥 گروه: <b>{group_title}</b>\n\n"
        "اتصال به‌صورت خودکار انجام شد و لینک عضویت دانش‌آموزان آماده است."
    )
    if not group_notice_sent:
        confirmation += (
            "\n\n⚠️ اتصال ثبت شده، اما ارسال پیام تأیید داخل گروه ناموفق بود؛ "
            "دسترسی ارسال پیام ربات را بررسی کنید."
        )

    await message.answer(
        confirmation,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🎓 لینک ورود دانش‌آموزان",
                        url=student_url,
                        style="success",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🛠 مدیریت گروه",
                        callback_data=f"group:manage:{int(class_id)}",
                    )
                ],
            ]
        ),
    )
    await message.answer("\u2063", reply_markup=await menu_for(session, actor))
