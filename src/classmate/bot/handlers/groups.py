"""Panel-driven, permission-checked Telegram group linking."""

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

from classmate.bot.handlers.ui import get_user
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
                        style="danger",
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
                        style="danger",
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
        keyboard=[[
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
        ]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await callback.answer()
    await callback.message.answer(
        LINE_BREAK.join([
            "📂 <b>انتخاب گروه کلاس</b>",
            "گروه موردنظر را از فهرست گروه‌های تلگرام انتخاب کنید. "
            "برای اتصال، شما باید مدیر گروه باشید و ربات نیز باید در گروه عضو و مدیر باشد.",
            "پس از انتخاب، پیش‌نیازها بررسی می‌شوند و قبل از ثبت نهایی، خلاصه عملیات را می‌بینید.",
        ]),
        reply_markup=picker,
    )


@router.message(GroupLinkForm.selecting, F.text.in_({"❌ لغو انتخاب", "❌ انصراف"}))
async def cancel_group_picker_message(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("انتخاب گروه لغو شد.", reply_markup=ReplyKeyboardRemove())

@router.message(GroupLinkForm.selecting, F.chat_shared)
async def group_chat_selected(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    shared = message.chat_shared
    data = await state.get_data()
    class_id = data.get("group_class_id")
    if not class_id or shared.request_id != int(class_id):
        await message.answer("درخواست انتخاب گروه معتبر نیست؛ دوباره از پنل کلاس اقدام کنید.")
        await state.clear()
        return
    actor = await get_user(session, message.from_user, private=True)
    if not await is_class_manager(session, actor, int(class_id)):
        await message.answer("دسترسی مدیریت این کلاس برای حساب شما فعال نیست.")
        await state.clear()
        return
    try:
        chat = await message.bot.get_chat(shared.chat_id)
        bot_user = await message.bot.get_me()
        bot_member = await message.bot.get_chat_member(chat_id=shared.chat_id, user_id=bot_user.id)
        actor_member = await message.bot.get_chat_member(
            chat_id=shared.chat_id, user_id=message.from_user.id
        )
    except TelegramAPIError:
        await message.answer(
            "❌ بررسی گروه ناموفق بود. ربات را به گروه اضافه و مدیر کنید، سپس دوباره انتخاب کنید.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await state.clear()
        return

    if chat.type not in {"group", "supergroup"} or shared.chat_id >= 0:
        await message.answer("فقط گروه یا سوپرگروه تلگرام قابل ثبت است.", reply_markup=ReplyKeyboardRemove())
        await state.clear()
        return
    if bot_member.status not in {"administrator", "creator"}:
        await message.answer(
            "❌ پیش‌نیاز ناقص است: ربات باید در گروه مدیر باشد.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await state.clear()
        return
    if actor_member.status not in {"administrator", "creator"}:
        await message.answer(
            "❌ پیش‌نیاز ناقص است: حساب شما مدیر این گروه نیست.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await state.clear()
        return

    class_context = await get_class_with_school(session, int(class_id))
    if class_context is None:
        await message.answer("کلاس دیگر فعال نیست؛ عملیات لغو شد.", reply_markup=ReplyKeyboardRemove())
        await state.clear()
        return
    class_name = class_context[0].name
    title = chat.title or str(shared.chat_id)
    await state.update_data(pending_group_chat_id=shared.chat_id, pending_group_title=title, pending_class_name=class_name)
    await message.answer(
        LINE_BREAK.join([
            "🧾 <b>پیش‌نمایش ثبت گروه</b>",
            f"🏫 کلاس: <b>{class_name}</b>",
            f"👥 گروه: <b>{title}</b>",
            "🤖 ربات: مدیر گروه است ✓",
            "👤 شما: مدیر گروه هستید ✓",
            "با تأیید، این گروه به کلاس متصل می‌شود و لینک ورود دانش‌آموزان ساخته خواهد شد. "
            "کد ورود قبلی کلاس در صورت وجود با کد تازه جایگزین می‌شود.",
            "ثبت هنوز انجام نشده است.",
        ]),
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer(
        "برای ثبت این اتصال، تأیید نهایی را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ تأیید و ثبت گروه", callback_data="group:select:confirm", style="success")],
            [InlineKeyboardButton(text="❌ انصراف", callback_data="group:select:cancel", style="danger")],
        ]),
    )


@router.callback_query(F.data == "group:select:cancel")
async def cancel_group_selection(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("عملیات لغو شد.")
    if callback.message is not None:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("ثبت گروه لغو شد.")


@router.callback_query(F.data == "group:select:confirm")
async def confirm_group_selection(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer("تأیید فقط از پنل خصوصی امکان‌پذیر است.", show_alert=True)
        return
    data = await state.get_data()
    class_id = data.get("group_class_id")
    chat_id = data.get("pending_group_chat_id")
    title = data.get("pending_group_title")
    if not class_id or not chat_id or not title:
        await callback.answer("اطلاعات انتخاب گروه منقضی شده است؛ دوباره تلاش کنید.", show_alert=True)
        await state.clear()
        return

    actor = await get_user(session, callback.from_user, private=True)
    if not await is_class_manager(session, actor, int(class_id)):
        await callback.answer("دسترسی مدیریت این کلاس برای شما فعال نیست.", show_alert=True)
        await state.clear()
        return
    try:
        bot_user = await callback.bot.get_me()
        bot_member = await callback.bot.get_chat_member(chat_id=int(chat_id), user_id=bot_user.id)
        actor_member = await callback.bot.get_chat_member(
            chat_id=int(chat_id), user_id=callback.from_user.id
        )
        if bot_member.status not in {"administrator", "creator"}:
            raise PermissionError("ربات دیگر مدیر گروه نیست.")
        if actor_member.status not in {"administrator", "creator"}:
            raise PermissionError("شما دیگر مدیر این گروه نیستید.")
        bot_info = await callback.bot.get_me()
        async with session.begin_nested():
            code, _, _ = await issue_class_group_link_code(
                session, class_id=int(class_id), actor=actor
            )
            linked = await connect_group_by_code(
                session,
                code=code,
                chat_id=int(chat_id),
                chat_title=str(title),
                actor=actor,
                bot_is_admin=True,
                actor_is_admin=True,
            )
            student_code = make_code(12)
            linked.member_join_code_hash = hash_code(student_code)
            linked.member_join_code_expires_at = None
        await session.commit()
    except (TelegramAPIError, PermissionError, ValueError) as exc:
        await callback.answer(str(exc)[:180], show_alert=True)
        return

    await state.clear()
    await callback.answer("گروه با موفقیت ثبت شد.")
    await callback.message.edit_text(
        LINE_BREAK.join([
            "✨ <b>اتصال گروه با موفقیت ثبت شد</b> ✨",
            f"🏫 کلاس: <b>{linked.name}</b>",
            f"👥 گروه: <b>{title}</b>",
            "🔐 پیش‌نیازهای اتصال بررسی و تأیید شدند.",
            "🎓 لینک عضویت دانش‌آموزان آماده است.",
            "دانش‌آموزان با زدن دکمه زیر وارد ربات می‌شوند و عضویت آن‌ها در همین کلاس ثبت خواهد شد.",
        ]),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="🚀 ورود به کلاس به‌عنوان دانش‌آموز",
                url=f"https://t.me/{bot_info.username}?start=join_{student_code}",
                style="success",
            )
        ]]),
    )
