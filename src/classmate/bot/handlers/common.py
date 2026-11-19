from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from classmate.bot.keyboards import main_menu

router = Router(name="common")


@router.message(Command("help"))
@router.message(F.text == "راهنما")
async def cmd_help(message: Message) -> None:
    await message.answer(
        "<b>راهنمای ClassMate 2</b>\n\n"
        "/start — شروع و منوی اصلی\n"
        "/help — همین راهنما\n"
        "/classes — فهرست کلاس‌های من\n"
        "/cancel — لغو فرم در حال انجام\n\n"
        "قابلیت‌های اصلی به زودی فعال می‌شوند:\n"
        "• ساخت مدرسه و کلاس\n"
        "• اتصال گروه تلگرام\n"
        "• ثبت و تأیید تکلیف / امتحان / گزارش\n"
        "• برنامه هفتگی و یادآوری",
        reply_markup=main_menu(),
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message) -> None:
    # Will be expanded when FSM forms are added
    await message.answer("هیچ فرم فعالی برای لغو وجود ندارد.", reply_markup=main_menu())
