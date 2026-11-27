"""Phase-two learning workflows and support surface."""

import logging
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.bot.handlers.ui import (
    delete_panel,
    classes_for_user,
    get_user,
    menu_for,
    render_panel,
)
from classmate.bot.keyboards import cancel_kb, class_picker, education_result_kb
from classmate.bot.states import SubmissionForm
from classmate.core.enums import RecordStatus
from classmate.db.models import (
    Attendance,
    ClassMembership,
    ClassSubject,
    Grade,
    Homework,
    HomeworkSubmission,
    Poll,
    PollOption,
    Subject,
    User,
)
from classmate.domain.dates import jalali_date
from classmate.services.permissions import get_class_with_school, user_can_access_class
from classmate.services.phase2 import (
    active_polls,
    answer_poll,
    approved_homeworks_for_student,
    poll_options,
    poll_result,
    save_submission,
    student_attendance,
    student_grades,
)

router = Router(name="phase2")
logger = logging.getLogger(__name__)


async def _require_private_callback(callback: CallbackQuery) -> bool:
    if callback.message is None or callback.message.chat.type != "private":
        await callback.answer(
            "این عملیات فقط از گفت‌وگوی خصوصی ربات در دسترس است.",
            show_alert=True,
        )
        return False
    return True


async def _require_private_message(message: Message, state: FSMContext | None = None) -> bool:
    if message.chat.type != "private":
        if state is not None:
            await state.clear()
        await message.answer("این عملیات فقط در گفت‌وگوی خصوصی ربات انجام می‌شود.")
        return False
    return True


def color_button(prefix: str, text: str, callback: str) -> InlineKeyboardButton:
    """Create an inline button with an emoji prefix and matching semantic style."""
    styles = {"🔴": "danger", "🟢": "success", "🔵": "primary", "🟡": "primary"}
    return InlineKeyboardButton(
        text=f"{prefix} {text}", callback_data=callback, style=styles.get(prefix)
    )


@router.message(F.text.in_({"📤 ارسال تکلیف", "ارسال تکلیف"}))
async def submit_entry(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    classes = await classes_for_user(session, user)
    if not classes:
        await message.answer("ابتدا با کد دعوت عضو یک کلاس شوید.")
        return
    elif len(classes) == 1:
        await _show_homeworks(message, session, classes[0][0])
    else:
        await render_panel(
            message,
            "🏫 کلاس موردنظر را انتخاب کنید:",
            class_picker(classes, action="submissions"),
        )


async def _show_homeworks(message: Message, session: AsyncSession, class_id: int) -> None:
    if message.chat.type != "private":
        await message.answer("این بخش فقط از گفت‌وگوی خصوصی ربات در دسترس است.")
        return
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    if not await user_can_access_class(session, user, class_id):
        await message.answer("دسترسی شما به این کلاس فعال نیست.")
        return
    rows = await approved_homeworks_for_student(session, class_id)
    if not rows:
        await render_panel(
            message,
            "📚 در این کلاس تکلیف تأییدشده‌ای برای ارسال پاسخ وجود ندارد.",
            education_result_kb(),
        )
        return
    buttons = []
    lines = ["📤 <b>ارسال پاسخ تکلیف</b>", "تکلیف موردنظر را انتخاب کنید:"]
    for homework, subject in rows:
        due = jalali_date(homework.due_on) if homework.due_on else "بدون موعد"
        lines.append(f"\n• {escape(subject.name)} — {escape(homework.title)} | موعد: {due}")
        buttons.append(
            [
                color_button(
                    "🔵", f"{subject.name}: {homework.title[:30]}", f"p2:submit:{homework.id}"
                )
            ]
        )
    buttons.append([color_button("🔴", "انصراف", "p2:cancel")])
    await render_panel(message, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("p2:submit:"))
async def submit_homework(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    if not await _require_private_callback(callback):
        return
    homework_id = int(callback.data.split(":")[2])
    homework = await session.get(Homework, homework_id)
    user = await get_user(
        session,
        callback.from_user,
        private=bool(callback.message and callback.message.chat.type == "private"),
    )
    if homework is None or not await user_can_access_class(session, user, homework.class_id):
        await callback.answer("این تکلیف برای شما در دسترس نیست.", show_alert=True)
        return
    await delete_panel(callback.message)
    await state.update_data(homework_id=homework_id)
    await state.set_state(SubmissionForm.answer)
    await callback.message.answer(
        "پاسخ خود را ارسال کنید؛ متن، فایل یا تصویر قابل قبول است.", reply_markup=cancel_kb()
    )
    await callback.answer()


@router.message(SubmissionForm.answer, F.text)
async def submit_text(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not await _require_private_message(message, state):
        return
    if message.text == "❌ انصراف":
        await state.clear()
        user = await get_user(session, message.from_user, private=message.chat.type == "private")
        await message.answer("عملیات لغو شد.", reply_markup=await menu_for(session, user))
        return
    await _save_answer(message, state, session, text=message.text)


@router.message(SubmissionForm.answer, F.document)
async def submit_document(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not await _require_private_message(message, state):
        return
    await _save_answer(
        message, state, session, file_id=message.document.file_id, file_type="document"
    )


@router.message(SubmissionForm.answer, F.photo)
async def submit_photo(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not await _require_private_message(message, state):
        return
    await _save_answer(
        message, state, session, file_id=message.photo[-1].file_id, file_type="photo"
    )


async def _save_answer(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    text: str | None = None,
    file_id: str | None = None,
    file_type: str | None = None,
) -> None:
    if message.chat.type != "private":
        await message.answer("ارسال پاسخ تکلیف فقط در گفت‌وگوی خصوصی ربات انجام می‌شود.")
        return
    data = await state.get_data()
    user = await get_user(session, message.from_user, private=True)
    homework_id = data.get("homework_id")
    homework = await session.get(Homework, homework_id) if homework_id else None
    if homework is None or not await user_can_access_class(session, user, homework.class_id):
        await state.clear()
        await message.answer(
            "دسترسی شما به این تکلیف فعال نیست.", reply_markup=await menu_for(session, user)
        )
        return
    try:
        await save_submission(
            session, homework_id, user.id, text=text, file_id=file_id, file_type=file_type
        )
        await session.commit()
    except (ValueError, PermissionError) as exc:
        await state.clear()
        await message.answer(str(exc), reply_markup=await menu_for(session, user))
        return
    await state.clear()
    await message.answer(
        "✅ پاسخ شما با موفقیت ثبت شد و برای بررسی دبیر ارسال گردید.",
        reply_markup=await menu_for(session, user),
    )


@router.message(F.text.in_({"📊 نمرات من", "نمرات"}))
async def my_grades(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    classes = await classes_for_user(session, user)
    rows = await student_grades(session, user.id, [row[0] for row in classes])
    if not rows:
        await render_panel(message, "📊 هنوز نمره‌ای برای شما ثبت نشده است.", education_result_kb())
        return
    class_names = {class_id: name for class_id, name, _school in classes}
    lines = ["📊 <b>نمرات من</b>"]
    total = 0.0
    for grade, subject in rows:
        total += grade.score / grade.max_score * 20 if grade.max_score else 0
        note = f" — {escape(grade.feedback)}" if grade.feedback else ""
        class_name = class_names.get(grade.class_id, "کلاس نامشخص")
        lines.append(
            f"\n• <b>{escape(class_name)}</b> — {escape(subject.name)}: <b>{grade.score:g}</b> از {grade.max_score:g}{note}"
        )
    average = total / len(rows) if rows else 0
    lines.append(f"\n📈 میانگین تقریبی: <b>{average:.2f}</b> از ۲۰")
    await render_panel(message, "\n".join(lines), education_result_kb())


@router.message(F.text.in_({"🟢 حضور من", "حضور"}))
async def my_attendance(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    classes = await classes_for_user(session, user)
    rows = await student_attendance(session, user.id, [row[0] for row in classes])
    if not rows:
        await render_panel(
            message, "🟢 هنوز سابقه حضور‌وغیابی برای شما ثبت نشده است.", education_result_kb()
        )
        return
    labels = {"present": "✅ حاضر", "absent": "🔴 غایب", "late": "🟡 تأخیر", "excused": "🔵 موجه"}
    class_names = {class_id: name for class_id, name, _school in classes}
    counts = {key: sum(row.status == key for row in rows) for key in labels}
    lines = ["🟢 <b>گزارش حضور‌وغیاب من</b>", ""]
    lines.extend(f"{labels[key]}: {counts[key]}" for key in labels)
    lines.append("\n<b>آخرین وضعیت‌ها</b>")
    lines.extend(
        f"• {jalali_date(row.attendance_date)} — {escape(class_names.get(row.class_id, 'کلاس نامشخص'))} — {labels.get(row.status, row.status)}"
        for row in rows[:10]
    )
    await render_panel(message, "\n".join(lines), education_result_kb())


@router.message(F.text.in_({"🗳 نظرسنجی‌ها", "نظرسنجی"}))
async def polls(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    classes = await classes_for_user(session, user)
    rows = await active_polls(session, [row[0] for row in classes])
    if not rows:
        await render_panel(
            message, "🗳 در حال حاضر نظرسنجی فعالی برای شما وجود ندارد.", education_result_kb()
        )
        return
    buttons = [[color_button("🔵", poll.question[:45], f"p2:poll:{poll.id}")] for poll in rows]
    buttons.append([color_button("🔴", "بازگشت", "menu:education")])
    await render_panel(
        message,
        "🗳 <b>نظرسنجی‌های فعال</b>\n\nبرای شرکت، یک مورد را انتخاب کنید:",
        InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("p2:poll:"))
async def poll_open(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _require_private_callback(callback):
        return
    poll_id = int(callback.data.split(":")[2])
    user = await get_user(
        session,
        callback.from_user,
        private=bool(callback.message and callback.message.chat.type == "private"),
    )
    poll = await session.get(Poll, poll_id)
    if poll is None or not await user_can_access_class(session, user, poll.class_id):
        await callback.answer("این نظرسنجی برای شما در دسترس نیست.", show_alert=True)
        return
    closes_at = poll.closes_at
    if closes_at is not None and closes_at.tzinfo is None:
        closes_at = closes_at.replace(tzinfo=timezone.utc)
    if not poll.is_active or (closes_at is not None and closes_at < datetime.now(timezone.utc)):
        await callback.answer("مهلت این نظرسنجی به پایان رسیده است.", show_alert=True)
        return
    options = await poll_options(session, poll_id)
    if not options:
        await delete_panel(callback.message)
        await render_panel(
            callback.message, "این نظرسنجی گزینه معتبری ندارد.", education_result_kb()
        )
        await callback.answer()
        return
    buttons = [
        [color_button("🟢", option.label, f"p2:vote:{poll_id}:{option.id}")] for option in options
    ]
    buttons.append([color_button("🔴", "بستن", "p2:cancel")])
    await delete_panel(callback.message)
    await callback.message.answer(
        f"🗳 <b>{escape(poll.question if poll else 'نظرسنجی')}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("p2:vote:"))
async def poll_vote(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _require_private_callback(callback):
        return
    _, _, poll_id_s, option_id_s = callback.data.split(":")
    user = await get_user(
        session,
        callback.from_user,
        private=bool(callback.message and callback.message.chat.type == "private"),
    )
    poll_id, option_id = int(poll_id_s), int(option_id_s)
    ok = await answer_poll(session, poll_id, user.id, option_id)
    if ok:
        await session.commit()
    await delete_panel(callback.message)
    if ok:
        option = await session.get(PollOption, option_id)
        text = (
            f"✅ رأی شما ثبت شد.\nگزینه انتخاب‌شده: <b>{escape(option.label if option else '—')}</b>"
        )
    else:
        text = "⚠️ این نظرسنجی دیگر فعال نیست و رأی ثبت نشد."
    await render_panel(callback.message, text, education_result_kb())
    await callback.answer()


@router.callback_query(F.data == "p2:cancel")
async def phase2_cancel(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not await _require_private_callback(callback):
        return
    await state.clear()
    await delete_panel(callback.message)
    user = await get_user(
        session,
        callback.from_user,
        private=bool(callback.message and callback.message.chat.type == "private"),
    )
    await callback.message.answer("عملیات لغو شد.", reply_markup=await menu_for(session, user))
    await callback.answer()


@router.message(F.text.in_({"🆘 پشتیبانی", "پشتیبانی"}))
async def support(message: Message, session: AsyncSession) -> None:
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    await message.answer(
        "🚧 ⌯ اگه کارت به مشکل خورده یا پیشنهادی برای ارتقا ربات داری میتونی از طریق آیدی زیر با ما در ارتباط باشی🤝🏻\n"
        "🗣️ @Mizanup",
        reply_markup=await menu_for(session, user),
    )
