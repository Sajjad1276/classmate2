"""Student onboarding: join a class with its shared ClassMate code."""

from html import escape

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.bot.handlers.ui import get_user, menu_for
from classmate.bot.states import JoinForm
from classmate.services.permissions import consume_class_member_join_code

router = Router(name="onboarding")


async def _join_with_class_code(
    message: Message,
    *,
    session: AsyncSession,
    code: str,
) -> tuple[bool, str]:
    user = await get_user(session, message.from_user, private=True)
    if not user.is_active:
        return False, "حساب کاربری شما غیرفعال است."
    try:
        class_group = await consume_class_member_join_code(
            session,
            code=code,
            user=user,
        )
    except (PermissionError, ValueError) as exc:
        return False, str(exc)
    return True, f"با موفقیت به کلاس <b>{escape(class_group.name)}</b> ملحق شدید."


@router.message(JoinForm.code, F.text, ~CommandStart())
async def join_class_code_input(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if message.chat.type != "private":
        await state.clear()
        return
    if (message.text or "").strip() in {"❌ انصراف", "انصراف"}:
        await state.clear()
        user = await get_user(session, message.from_user, private=True)
        await message.answer("ورود به کلاس لغو شد.", reply_markup=await menu_for(session, user))
        return
    code = (message.text or "").strip().upper()
    if code.startswith("JOIN_"):
        code = code.removeprefix("JOIN_").strip()
    if not code or len(code) > 64 or any(ch.isspace() for ch in code):
        await message.answer("کد ورود معتبر نیست؛ کد را بدون فاصله دوباره وارد کنید.")
        return

    succeeded, response = await _join_with_class_code(
        message,
        session=session,
        code=code,
    )
    if succeeded:
        await state.clear()
        user = await get_user(session, message.from_user, private=True)
        await message.answer(response, reply_markup=await menu_for(session, user))
        return

    await message.answer(
        f"{response}\n\nکد درست را دوباره بفرستید یا «❌ انصراف» را بزنید."
    )
