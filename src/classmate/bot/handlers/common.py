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
        "<b>شروع</b>\n"
        "/start — منوی اصلی\n"
        "/classes — کلاس‌های من\n"
        "/cancel — لغو فرم\n\n"
        "<b>راه‌اندازی</b>\n"
        "/school_new نام | سال [| timezone]\n"
        "/class_new شناسه_مدرسه | پایه | شعبه | نام\n"
        "/class_code شناسه_کلاس\n"
        "/connect کد — در گروه\n"
        "/join کد — در خصوصی\n\n"
        "<b>ثبت</b>\n"
        "/subject_add نام درس\n"
        "/new_homework درس | عنوان | توضیح | موعد؟\n"
        "/new_exam درس | عنوان | محدوده | زمان\n"
        "/new_report متن [| عنوان]\n"
        "/timetable_set روز | زنگ | درس | شروع-پایان\n\n"
        "<b>مشاهده</b>\n"
        "/homework /exams /today /week /pending",
        reply_markup=main_menu(),
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("فرم لغو شد.", reply_markup=main_menu())


@router.message(F.text == "کلاس‌های من")
async def btn_classes(message: Message) -> None:
    await message.answer("از فرمان /classes استفاده کن.")
