from html import escape
from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession
from classmate.bot.handlers.onboarding import _join_with_class_code
from classmate.bot.handlers.ui import get_user, menu_for

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()

    if message.chat.type in {"group", "supergroup"}:
        await message.answer(
            "برای اتصال این گروه به یک کلاس، مدیر گروه دکمهٔ زیر را انتخاب کند. "
            "ربات باید مدیر گروه باشد.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🔗 اتصال این گروه به کلاس",
                            callback_data="group:link:start",
                        )
                    ]
                ]
            ),
        )
        return

    user = await get_user(session, message.from_user, private=True)
    parts = (message.text or "").strip().split(maxsplit=1)
    payload = parts[1].strip() if len(parts) == 2 else ""
    join_response = None
    if payload.startswith("join_"):
        join_code = payload.removeprefix("join_").strip().upper()
        if not join_code or len(join_code) > 64 or any(ch.isspace() for ch in join_code):
            join_response = "لینک عضویت معتبر نیست؛ کد را از مدیر کلاس دوباره دریافت کنید."
        else:
            joined, response = await _join_with_class_code(
                message,
                session=session,
                code=join_code,
            )
            join_response = ("✅ " if joined else "⚠️ ") + response

    name = escape(message.from_user.first_name or "")
    username = escape(f"@{message.from_user.username}" if message.from_user.username else "بدون نام کاربری")
    if join_response:
        await message.answer(join_response)
    await message.answer(
        f"• درود بیکران 🕊 {name}، به ربات ClassMatePro خوش آمدید.\n\n"
        f"📇 - در اینجا میتونید امور مدرسه رو به صورت حرفهای و پیشرفته مدیریت کنید.\n\n"
        f"• آیدی شما : {username}\n\n"
        "🪎جهت ادامه دستور مورد نظر رو از پنل زیر وارد کنید:",
        reply_markup=await menu_for(session, user),
    )
