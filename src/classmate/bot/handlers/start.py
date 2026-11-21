from html import escape
from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession
from classmate.bot.handlers.ui import get_user, menu_for

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    name = escape(message.from_user.first_name or "")
    await message.answer(
        f"سلام {name} گرامی 👋\n\nبه <b>ClassMate 2</b> خوش آمدید.\n\nاز «🔑 ورود با کد دعوت» وارد کلاس شوید و محتوای آموزشی خود را در «🎓 بخش آموزشی» ببینید.\n\n🆘 پشتیبانی: @Mizanup",
        reply_markup=await menu_for(session, user),
    )
