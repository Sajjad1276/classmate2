from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from classmate.bot.keyboards import main_menu

router = Router(name="common")


@router.message(Command("help"))
@router.message(F.text == "راهنما")
async def cmd_help(message: Message) -> None:
    await message.answer(
        "<b>راهنمای ClassMate 2</b>\n\n"
        "/start — شروع\n"
        "/classes — کلاس‌های من\n"
        "/school_new نام | سال — ساخت مدرسه (مدیر کل)\n"
        "/class_new شناسه_مدرسه | پایه | شعبه | نام — ساخت کلاس\n"
        "/class_code شناسه_کلاس — کد اتصال و عضویت\n"
        "/connect کد — اتصال گروه\n"
        "/join کد — عضویت در کلاس\n"
        "/homework [شناسه] — تکالیف تأییدشده\n"
        "/exams [شناسه] — امتحانات\n"
        "/today [شناسه] — گزارش امروز\n"
        "/week [شناسه] — برنامه هفتگی\n"
        "/pending [شناسه] — در انتظار تأیید\n"
        "/cancel — لغو فرم",
        reply_markup=main_menu(),
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("فرم لغو شد.", reply_markup=main_menu())


@router.message(F.text == "کلاس‌های من")
async def btn_classes(message: Message) -> None:
    await message.answer("برای فهرست کلاس‌ها از /classes استفاده کن.")
