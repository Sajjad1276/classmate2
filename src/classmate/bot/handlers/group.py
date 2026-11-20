"""Inline control panel inside connected class groups."""

from html import escape

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.bot.handlers.ui import get_user, run_view
from classmate.bot.keyboards import group_panel
from classmate.services.records import get_group_context

router = Router(name="group")

PANEL_TRIGGERS = {
    "منو",
    "پنل",
    "پنل کلاس",
    "منوی کلاس",
    "classmate",
    "کلاس میت",
    "منو کلاس",
}


async def _panel_text(class_name: str) -> str:
    return (
        f"📌 <b>پنل کلاس {escape(class_name)}</b>\n\n"
        "مشاهده یا ثبت را از دکمه‌ها انتخاب کن.\n"
        "برای باز کردن دوباره همین پنل، در گروه بنویس: <b>منو</b>"
    )


async def send_group_panel(message: Message, session: AsyncSession) -> bool:
    ctx = await get_group_context(session, message.chat.id)
    if ctx is None:
        return False
    class_group, _, school = ctx
    await message.answer(
        await _panel_text(class_group.name),
        reply_markup=group_panel(class_id=class_group.id, class_name=class_group.name),
    )
    return True


@router.message(F.chat.type.in_({"group", "supergroup"}), CommandStart())
async def group_start(message: Message, session: AsyncSession) -> None:
    ok = await send_group_panel(message, session)
    if not ok:
        await message.answer(
            "این گروه هنوز به کلاسی وصل نیست.\n"
            "کد اتصال را (از مدیریت کلاس) همین‌جا به صورت یک پیام معمولی بفرست."
        )


@router.message(
    F.chat.type.in_({"group", "supergroup"}),
    F.text.func(lambda t: bool(t) and t.strip().lower() in PANEL_TRIGGERS),
)
async def group_menu_text(message: Message, session: AsyncSession) -> None:
    ok = await send_group_panel(message, session)
    if not ok:
        await message.answer("گروه به کلاسی متصل نیست. اول کد اتصال را بفرست.")


@router.callback_query(F.data.startswith("g:panel:"))
async def cb_refresh_panel(callback: CallbackQuery, session: AsyncSession) -> None:
    class_id = int(callback.data.split(":")[-1])
    from classmate.services.permissions import get_class_with_school

    ctx = await get_class_with_school(session, class_id)
    if ctx is None:
        await callback.answer("کلاس پیدا نشد", show_alert=True)
        return
    class_group = ctx[0]
    text = await _panel_text(class_group.name)
    try:
        await callback.message.edit_text(
            text,
            reply_markup=group_panel(class_id=class_id, class_name=class_group.name),
        )
    except Exception:
        await callback.message.answer(
            text,
            reply_markup=group_panel(class_id=class_id, class_name=class_group.name),
        )
    await callback.answer("بروز شد")


@router.callback_query(F.data.startswith("g:view:"))
async def cb_group_view(callback: CallbackQuery, session: AsyncSession) -> None:
    # g:view:{action}:{class_id}
    parts = callback.data.split(":")
    action = parts[2]
    class_id = int(parts[3])
    user = await get_user(session, callback.from_user, private=False)
    await run_view(callback.message, session, action, class_id, user)
    await callback.answer()


@router.callback_query(F.data.startswith("g:new:"))
async def cb_group_new(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    # g:new:hw|exam|report:{class_id}
    parts = callback.data.split(":")
    kind = parts[2]
    class_id = int(parts[3])

    from classmate.bot.handlers.wizards import (
        start_exam_after_class,
        start_homework_after_class,
        start_report_after_class,
    )

    await state.clear()
    await callback.answer()

    # Reply in group so the whole class sees progress; FSM is per user+chat
    if kind == "hw":
        await start_homework_after_class(callback.message, state, session, class_id)
    elif kind == "exam":
        await start_exam_after_class(callback.message, state, session, class_id)
    elif kind == "report":
        await start_report_after_class(callback.message, state, session, class_id)
    else:
        await callback.message.answer("نوع نامعتبر.")
