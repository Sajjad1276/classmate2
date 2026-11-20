"""Inline control panel inside connected class groups."""

import logging
from html import escape

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.bot.handlers.ui import delete_panel, get_user, run_view
from classmate.bot.keyboards import group_panel
from classmate.services.permissions import get_class_with_school, user_can_access_class
from classmate.services.records import get_group_context

router = Router(name="group")
logger = logging.getLogger(__name__)

PANEL_TRIGGERS = {
    "منو",
    "پنل",
    "پنل کلاس",
    "منوی کلاس",
    "classmate",
    "کلاس میت",
    "منو کلاس",
}


def panel_text(class_name: str) -> str:
    return (
        f"📌 <b>پنل کلاس {escape(class_name)}</b>\n\n"
        "برای مشاهده اطلاعات یا ثبت مورد جدید، گزینه مناسب را انتخاب کنید.\n"
        "برای نمایش دوباره این پنل، «منو» را ارسال کنید یا ربات را همراه با عبارت «منو» منشن کنید."
    )


async def send_group_panel(message: Message, session: AsyncSession) -> bool:
    ctx = await get_group_context(session, message.chat.id)
    if ctx is None:
        return False
    class_group, _, _ = ctx
    await message.answer(
        panel_text(class_group.name),
        reply_markup=group_panel(class_id=class_group.id, class_name=class_group.name),
    )
    return True


@router.message(F.chat.type.in_({"group", "supergroup"}), CommandStart())
async def group_start(message: Message, session: AsyncSession) -> None:
    ok = await send_group_panel(message, session)
    if not ok:
        await message.answer(
            "این گروه هنوز به کلاسی وصل نیست.\n"
            "کد اتصال را همین‌جا به‌صورت یک پیام معمولی ارسال کنید."
        )


@router.message(
    F.chat.type.in_({"group", "supergroup"}),
    F.text.func(
        lambda t: bool(t)
        and (
            t.strip().lower() in PANEL_TRIGGERS
            or any(t.strip().lower().endswith(f" {x}") or t.strip().lower().startswith(f"{x} ") for x in PANEL_TRIGGERS)
        )
    ),
)
async def group_menu_text(message: Message, session: AsyncSession) -> None:
    # Works when Privacy Mode is off, bot is admin, or message is a reply/mention path Telegram delivers
    ok = await send_group_panel(message, session)
    if not ok:
        await message.answer("این گروه به کلاسی متصل نیست. ابتدا کد اتصال را ارسال کنید.")


@router.callback_query(F.data.startswith("g:panel:"))
async def cb_refresh_panel(callback: CallbackQuery, session: AsyncSession) -> None:
    class_id = int(callback.data.split(":")[-1])
    from classmate.services.permissions import get_class_with_school

    ctx = await get_class_with_school(session, class_id)
    if (
        ctx is None
        or callback.message.chat.type not in {"group", "supergroup"}
        or ctx[0].group_chat_id != callback.message.chat.id
    ):
        await callback.answer("این پنل متعلق به این گروه نیست یا کلاس غیرفعال شده است.", show_alert=True)
        return
    class_group = ctx[0]
    text = panel_text(class_group.name)
    try:
        await callback.message.edit_text(
            text,
            reply_markup=group_panel(class_id=class_id, class_name=class_group.name),
        )
    except Exception:
        logger.warning("Could not edit group panel message; using a new message", exc_info=True)
        await callback.message.answer(
            text,
            reply_markup=group_panel(class_id=class_id, class_name=class_group.name),
        )
    await callback.answer("پنل با موفقیت به‌روزرسانی شد")


@router.callback_query(F.data.startswith("g:view:"))
async def cb_group_view(callback: CallbackQuery, session: AsyncSession) -> None:
    parts = callback.data.split(":")
    action = parts[2]
    class_id = int(parts[3])
    user = await get_user(session, callback.from_user, private=False)
    ctx = await get_class_with_school(session, class_id)
    if (
        ctx is None
        or callback.message.chat.type not in {"group", "supergroup"}
        or ctx[0].group_chat_id != callback.message.chat.id
        or not await user_can_access_class(session, user, class_id)
    ):
        await callback.answer("این کلاس برای شما یا این گروه در دسترس نیست.", show_alert=True)
        return
    await delete_panel(callback.message)
    await run_view(callback.message, session, action, class_id, user, close_reply_keyboard=False)
    await callback.answer()


@router.callback_query(F.data.startswith("g:new:"))
async def cb_group_new(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    parts = callback.data.split(":")
    kind = parts[2]
    class_id = int(parts[3])

    from classmate.bot.handlers.wizards import (
        start_exam_after_class,
        start_homework_after_class,
        start_report_after_class,
    )

    user = await get_user(session, callback.from_user, private=False)
    ctx = await get_class_with_school(session, class_id)
    if (
        ctx is None
        or callback.message.chat.type not in {"group", "supergroup"}
        or ctx[0].group_chat_id != callback.message.chat.id
        or not await user_can_access_class(session, user, class_id)
    ):
        await callback.answer("فقط عضو فعال این کلاس می‌تواند درخواست ثبت کند.", show_alert=True)
        return

    await state.clear()
    await delete_panel(callback.message)
    await callback.answer()

    if kind == "hw":
        await start_homework_after_class(callback.message, state, session, class_id)
    elif kind == "exam":
        await start_exam_after_class(callback.message, state, session, class_id)
    elif kind == "report":
        await start_report_after_class(callback.message, state, session, class_id)
    else:
        await callback.message.answer("نوع اطلاعات معتبر نیست.")
