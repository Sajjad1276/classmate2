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
        bot_info = await message.bot.get_me()
        bot_url = f"https://t.me/{bot_info.username}" if bot_info.username else None
        await message.answer(
            "✅ ربات ClassMate در این گروه فعال است.\n\n"
            "برای اتصال این گروه به کلاس، مدیر کلاس باید در گفت‌وگوی خصوصی ربات وارد بخش «🏫 کلاس‌های من» شود، "
            "گزینهٔ «ثبت گروه» یا «مدیریت گروه» همان کلاس را باز کند و این گروه را انتخاب کند.\n"
            "پس از تأیید مجوزهای درخواستی در تلگرام، اتصال گروه به کلاس ثبت می‌شود.",
            reply_markup=(
                InlineKeyboardMarkup(
                    inline_keyboard=[[
                        InlineKeyboardButton(
                            text="⚙️ ادامه در گفت‌وگوی خصوصی ربات",
                            url=bot_url,
                            style="primary",
                        )
                    ]]
                )
                if bot_url else None
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
        f"• سلام {name}، به ربات ClassMate خوش آمدید.\n\n"
        "📚 در این ربات می‌توانید برنامهٔ کلاسی و امور آموزشی خود را پیگیری کنید.\n\n"
        f"• نام کاربری تلگرام: {username}\n\n"
        "🪄 برای ادامه، گزینهٔ موردنظر را از پنل زیر انتخاب کنید:",
        # The welcome copy is kept concise and consistent with the ClassMate name.
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📊 مشاهده وضعیت امروز",
                        callback_data="dashboard:today",
                        style="success",
                    )
                ]
            ]
        ),
    )
    # Telegram cannot attach reply and inline keyboards to the same message.
    # Install the persistent reply keyboard in a separate, visually empty
    # message while keeping the existing /start copy and its inline button.
    await message.answer("\u2063", reply_markup=await menu_for(session, user))
