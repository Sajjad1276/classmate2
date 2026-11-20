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

from classmate.bot.handlers.ui import delete_panel, classes_for_user, get_user, menu_for, render_panel
from classmate.bot.keyboards import cancel_kb, class_picker, education_result_kb
from classmate.bot.states import EvaluationForm, PollCreateForm, ReviewSubmissionForm, SubmissionForm
from classmate.core.enums import RecordStatus
from classmate.db.models import Attendance, ClassMembership, ClassSubject, Grade, Homework, HomeworkSubmission, Poll, PollOption, Subject, User
from classmate.domain.dates import jalali_date
from classmate.services.outbox import enqueue_telegram_message
from classmate.services.permissions import get_class_with_school, is_class_manager, user_can_access_class
from classmate.services.phase2 import (
    active_polls,
    answer_poll,
    approved_homeworks_for_student,
    poll_options,
    poll_result,
    save_attendance,
    save_submission,
    student_attendance,
    student_grades,
    validate_grade_values,
)

router = Router(name="phase2")
logger = logging.getLogger(__name__)


def color_button(prefix: str, text: str, callback: str) -> InlineKeyboardButton:
    """Use semantic prefixes and Telegram's supported button styles together."""
    styles = {"🔴": "danger", "🟢": "success", "🔵": "primary", "🟡": "primary"}
    return InlineKeyboardButton(text=f"{prefix} {text}", callback_data=callback, style=styles.get(prefix))


@router.message(F.text.in_({"📤 ارسال تکلیف", "ارسال تکلیف"}))
async def submit_entry(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    classes = await classes_for_user(session, user)
    if not classes:
        await message.answer("ابتدا با کد دعوت عضو یک کلاس شوید.")
    elif len(classes) == 1:
        await _show_homeworks(message, session, classes[0][0])
    else:
        await render_panel(
            message,
            "🏫 کلاس موردنظر را انتخاب کنید:",
            class_picker(classes, action="submissions"),
        )


async def _show_homeworks(message: Message, session: AsyncSession, class_id: int) -> None:
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
        buttons.append([color_button("🔵", f"{subject.name}: {homework.title[:30]}", f"p2:submit:{homework.id}")])
    buttons.append([color_button("🔴", "انصراف", "p2:cancel")])
    await render_panel(message, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("p2:submit:"))
async def submit_homework(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    homework_id = int(callback.data.split(":")[2])
    homework = await session.get(Homework, homework_id)
    user = await get_user(session, callback.from_user, private=bool(callback.message and callback.message.chat.type == "private"))
    if homework is None or not await user_can_access_class(session, user, homework.class_id):
        await callback.answer("این تکلیف برای شما در دسترس نیست.", show_alert=True)
        return
    await delete_panel(callback.message)
    await state.update_data(homework_id=homework_id)
    await state.set_state(SubmissionForm.answer)
    await callback.message.answer("پاسخ خود را ارسال کنید؛ متن، فایل یا تصویر قابل قبول است.", reply_markup=cancel_kb())
    await callback.answer()


@router.message(SubmissionForm.answer, F.text)
async def submit_text(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if message.text == "❌ انصراف":
        await state.clear()
        user = await get_user(session, message.from_user, private=message.chat.type == "private")
        await message.answer("عملیات لغو شد.", reply_markup=await menu_for(session, user))
        return
    await _save_answer(message, state, session, text=message.text)


@router.message(SubmissionForm.answer, F.document)
async def submit_document(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await _save_answer(message, state, session, file_id=message.document.file_id, file_type="document")


@router.message(SubmissionForm.answer, F.photo)
async def submit_photo(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await _save_answer(message, state, session, file_id=message.photo[-1].file_id, file_type="photo")


async def _save_answer(message: Message, state: FSMContext, session: AsyncSession, text: str | None = None, file_id: str | None = None, file_type: str | None = None) -> None:
    if message.chat.type != "private":
        await message.answer("ارسال پاسخ تکلیف فقط در گفت‌وگوی خصوصی ربات انجام می‌شود.")
        return
    data = await state.get_data()
    user = await get_user(session, message.from_user, private=True)
    homework_id = data.get("homework_id")
    homework = await session.get(Homework, homework_id) if homework_id else None
    if homework is None or not await user_can_access_class(session, user, homework.class_id):
        await state.clear()
        await message.answer("دسترسی شما به این تکلیف فعال نیست.", reply_markup=await menu_for(session, user))
        return
    try:
        await save_submission(session, homework_id, user.id, text=text, file_id=file_id, file_type=file_type)
        await session.commit()
    except (ValueError, PermissionError) as exc:
        await state.clear()
        await message.answer(str(exc), reply_markup=await menu_for(session, user))
        return
    await state.clear()
    await message.answer("✅ پاسخ شما با موفقیت ثبت شد و برای بررسی دبیر ارسال گردید.", reply_markup=await menu_for(session, user))


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
        lines.append(f"\n• <b>{escape(class_name)}</b> — {escape(subject.name)}: <b>{grade.score:g}</b> از {grade.max_score:g}{note}")
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
        await render_panel(message, "🟢 هنوز سابقه حضور‌وغیابی برای شما ثبت نشده است.", education_result_kb())
        return
    labels = {"present": "✅ حاضر", "absent": "🔴 غایب", "late": "🟡 تأخیر", "excused": "🔵 موجه"}
    class_names = {class_id: name for class_id, name, _school in classes}
    counts = {key: sum(row.status == key for row in rows) for key in labels}
    lines = ["🟢 <b>گزارش حضور‌وغیاب من</b>", ""]
    lines.extend(f"{labels[key]}: {counts[key]}" for key in labels)
    lines.append("\n<b>آخرین وضعیت‌ها</b>")
    lines.extend(f"• {jalali_date(row.attendance_date)} — {escape(class_names.get(row.class_id, 'کلاس نامشخص'))} — {labels.get(row.status, row.status)}" for row in rows[:10])
    await render_panel(message, "\n".join(lines), education_result_kb())


@router.message(F.text.in_({"🗳 نظرسنجی‌ها", "نظرسنجی"}))
async def polls(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    classes = await classes_for_user(session, user)
    rows = await active_polls(session, [row[0] for row in classes])
    if not rows:
        await render_panel(message, "🗳 در حال حاضر نظرسنجی فعالی برای شما وجود ندارد.", education_result_kb())
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
    poll_id = int(callback.data.split(":")[2])
    user = await get_user(session, callback.from_user, private=bool(callback.message and callback.message.chat.type == "private"))
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
        await render_panel(callback.message, "این نظرسنجی گزینه معتبری ندارد.", education_result_kb())
        await callback.answer()
        return
    buttons = [[color_button("🟢", option.label, f"p2:vote:{poll_id}:{option.id}")] for option in options]
    buttons.append([color_button("🔴", "بستن", "p2:cancel")])
    await delete_panel(callback.message)
    await callback.message.answer(f"🗳 <b>{escape(poll.question if poll else 'نظرسنجی')}</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@router.callback_query(F.data.startswith("p2:vote:"))
async def poll_vote(callback: CallbackQuery, session: AsyncSession) -> None:
    _, _, poll_id_s, option_id_s = callback.data.split(":")
    user = await get_user(session, callback.from_user, private=bool(callback.message and callback.message.chat.type == "private"))
    poll_id, option_id = int(poll_id_s), int(option_id_s)
    ok = await answer_poll(session, poll_id, user.id, option_id)
    if ok:
        await session.commit()
    await delete_panel(callback.message)
    if ok:
        option = await session.get(PollOption, option_id)
        text = f"✅ رأی شما ثبت شد.\nگزینه انتخاب‌شده: <b>{escape(option.label if option else '—')}</b>"
    else:
        text = "⚠️ این نظرسنجی دیگر فعال نیست و رأی ثبت نشد."
    await render_panel(callback.message, text, education_result_kb())
    await callback.answer()


@router.callback_query(F.data == "p2:cancel")
async def phase2_cancel(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await delete_panel(callback.message)
    user = await get_user(session, callback.from_user, private=bool(callback.message and callback.message.chat.type == "private"))
    await callback.message.answer("عملیات لغو شد.", reply_markup=await menu_for(session, user))
    await callback.answer()


@router.message(F.text.in_({"🆘 پشتیبانی", "پشتیبانی"}))
async def support(message: Message, session: AsyncSession) -> None:
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    await message.answer(
        "🆘 <b>مرکز پشتیبانی ClassMate 2</b>\n\n"
        "برای گزارش خطا، پیشنهاد قابلیت یا دریافت راهنمایی، با سازنده ربات در ارتباط باشید:\n\n"
        "👤 سازنده: <b>@Mizanup</b>\n"
        "📌 لطفاً هنگام ارسال مشکل، نام کلاس و شرح کوتاه مسئله را هم بنویسید.",
        reply_markup=await menu_for(session, user),
    )


@router.message(F.text == "👨‍🏫 ارزیابی کلاس")
async def manager_evaluation(message: Message, session: AsyncSession) -> None:
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    classes = [row for row in await classes_for_user(session, user) if await is_class_manager(session, user, row[0])]
    if not classes:
        await message.answer("شما مدیر هیچ کلاسی نیستید.")
        return
    await render_panel(
        message,
        "🏫 کلاس موردنظر برای ارزیابی را انتخاب کنید:",
        class_picker(classes, action="evaluation"),
    )


@router.message(F.text == "📝 بررسی پاسخ‌ها")
async def review_entry(message: Message, session: AsyncSession) -> None:
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    classes = [row for row in await classes_for_user(session, user) if await is_class_manager(session, user, row[0])]
    if not classes:
        await message.answer("شما مدیر هیچ کلاسی نیستید.")
        return
    await render_panel(
        message,
        "🏫 کلاس موردنظر را انتخاب کنید:",
        class_picker(classes, action="review_submissions"),
    )


async def submissions_panel(message: Message, session: AsyncSession, class_id: int) -> None:
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    if not await is_class_manager(session, user, class_id):
        await message.answer("فقط مدیر کلاس می‌تواند پاسخ‌های ارسالی را بررسی کند.")
        return
    rows = list((await session.execute(
        select(HomeworkSubmission, Homework, Subject, User)
        .join(Homework, HomeworkSubmission.homework_id == Homework.id)
        .join(Subject, Homework.subject_id == Subject.id)
        .join(User, HomeworkSubmission.student_id == User.id)
        .where(Homework.class_id == class_id)
        .order_by(HomeworkSubmission.created_at.desc()).limit(30)
    )).all())
    if not rows:
        await message.answer("📤 هنوز پاسخی برای تکالیف این کلاس ارسال نشده است.")
        return
    buttons = []
    for submission, homework, subject, student in rows:
        label = f"{student.full_name[:16]} — {homework.title[:22]}"
        buttons.append([color_button("🟡", label, f"p2:review:{submission.id}")])
    await render_panel(
        message,
        "📝 <b>پاسخ‌های ارسالی</b>\n\nبرای بررسی و ثبت بازخورد یک مورد را انتخاب کنید:",
        InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("p2:review:"))
async def review_open(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    submission = await session.get(HomeworkSubmission, int(callback.data.split(":")[2]))
    user = await get_user(session, callback.from_user, private=bool(callback.message and callback.message.chat.type == "private"))
    if submission is None:
        await callback.answer("پاسخ موردنظر پیدا نشد.", show_alert=True)
        return
    homework = await session.get(Homework, submission.homework_id)
    if (
        homework is None
        or homework.status != RecordStatus.APPROVED
        or not await is_class_manager(session, user, homework.class_id)
    ):
        await callback.answer("این پاسخ دیگر برای بررسی در دسترس نیست.", show_alert=True)
        return
    content = submission.text or ""
    await delete_panel(callback.message)
    if submission.file_id:
        try:
            if submission.file_type == "photo":
                await callback.message.answer_photo(
                    submission.file_id,
                    caption=f"📎 فایل تصویری پاسخ تکلیف «{escape(homework.title)}»",
                )
            else:
                await callback.message.answer_document(
                    submission.file_id,
                    caption=f"📎 فایل پاسخ تکلیف «{escape(homework.title)}»",
                )
        except Exception:
            logger.exception("Could not deliver submission file to reviewer", extra={"submission_id": submission.id})
            await callback.answer("ارسال فایل به دبیر ناموفق بود؛ دوباره تلاش کنید.", show_alert=True)
            return
    if not content and not submission.file_id:
        content = "بدون متن یا فایل"
    await callback.message.answer(
        f"📝 <b>{escape(homework.title)}</b>\n\n{escape(content) if content else 'فایل پیوست در پیام قبلی ارسال شد.'}\n\nبازخورد مدیر را ارسال کنید:",
        reply_markup=cancel_kb(),
    )
    await state.update_data(review_submission_id=submission.id)
    await state.set_state(ReviewSubmissionForm.feedback)
    await callback.answer()


@router.message(ReviewSubmissionForm.feedback, F.text)
async def review_submit(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if message.text == "❌ انصراف":
        await state.clear()
        user = await get_user(session, message.from_user, private=message.chat.type == "private")
        await message.answer("عملیات لغو شد.", reply_markup=await menu_for(session, user))
        return
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    data = await state.get_data()
    submission = await session.scalar(
        select(HomeworkSubmission)
        .where(HomeworkSubmission.id == data["review_submission_id"])
        .with_for_update()
    )
    if submission is None:
        await state.clear()
        await message.answer("پاسخ موردنظر پیدا نشد.")
        return
    homework = await session.scalar(
        select(Homework)
        .where(Homework.id == submission.homework_id)
        .with_for_update()
    )
    if (
        homework is None
        or homework.status != RecordStatus.APPROVED
        or not await is_class_manager(session, user, homework.class_id)
    ):
        await state.clear()
        await message.answer("این تکلیف دیگر فعال نیست یا دسترسی بررسی آن را ندارید.")
        return
    submission.feedback = message.text.strip()
    submission.status = "reviewed"
    submission.reviewed_by = user.id
    submission.reviewed_at = datetime.now(timezone.utc)
    student = await session.get(User, submission.student_id)
    if student and student.is_active and student.private_chat_started:
        await enqueue_telegram_message(
            session,
            target_type="private",
            target_chat_id=student.telegram_id,
            recipient_user_id=student.id,
            class_id=homework.class_id,
            entity_type="homework",
            entity_id=homework.id,
            expected_record_status="APPROVED",
            text=f"📝 بازخورد تکلیف «{escape(homework.title)}»:\n{escape(submission.feedback)}",
            idempotency_key=f"homework-feedback:{submission.id}:{submission.reviewed_at.isoformat()}",
        )
    await session.commit()
    await state.clear()
    await message.answer(
        "✅ بازخورد ثبت شد و برای ارسال به دانش‌آموز در صف قرار گرفت.",
        reply_markup=await menu_for(session, user),
    )


async def evaluation_panel(message: Message, session: AsyncSession, state: FSMContext, class_id: int) -> None:
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    if not await is_class_manager(session, user, class_id):
        await message.answer("فقط مدیر کلاس می‌تواند ارزیابی انجام دهد.")
        return
    await state.update_data(evaluation_class_id=class_id)
    await render_panel(
        message,
        "👨‍🏫 <b>ارزیابی کلاس</b>\n\n"
        "نوع عملیات را انتخاب کنید. برای حفظ دقت اطلاعات، شناسه دانش‌آموز و درس از بخش «کلاس‌های من» قابل مشاهده است.",
        InlineKeyboardMarkup(inline_keyboard=[
            [color_button("🟢", "ثبت حضور‌وغیاب", "p2:evaluate:attendance")],
            [color_button("🟡", "ثبت نمره و بازخورد", "p2:evaluate:grade")],
            [color_button("🔵", "ایجاد نظرسنجی", "p2:evaluate:poll")],
            [color_button("🔴", "انصراف", "p2:cancel")],
        ]),
    )


@router.callback_query(F.data.startswith("p2:evaluate:"))
async def evaluation_kind(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    user = await get_user(session, callback.from_user, private=bool(callback.message and callback.message.chat.type == "private"))
    class_id = data.get("evaluation_class_id")
    if not class_id or not await is_class_manager(session, user, class_id):
        await state.clear()
        await callback.answer("دسترسی ارزیابی این کلاس را ندارید.", show_alert=True)
        return
    kind = callback.data.split(":")[2]
    await state.update_data(evaluation_kind=kind)
    await state.set_state(EvaluationForm.data)
    await delete_panel(callback.message)
    if kind == "grade":
        text = (
            "📊 <b>ثبت نمره</b>\n\n"
            "قالب: شناسه دانش‌آموز | شناسه درس | نمره | سقف نمره | بازخورد اختیاری\n"
            "نمونه: <code>۱۲۳ | ۴۵ | ۱۸ | ۲۰ | عملکرد خوب</code>\n\n"
            "برای انصراف، «❌ انصراف» را بزنید."
        )
    elif kind == "poll":
        await state.set_state(PollCreateForm.question)
        await callback.message.answer("سؤال نظرسنجی را ارسال کنید:", reply_markup=cancel_kb())
        await callback.answer()
        return
    else:
        text = (
            "🟢 <b>ثبت حضور‌وغیاب</b>\n\n"
            "قالب: شناسه دانش‌آموز | وضعیت | توضیح اختیاری\n"
            "وضعیت مجاز: <code>present</code>، <code>absent</code>، <code>late</code>، <code>excused</code>\n"
            "نمونه: <code>۱۲۳ | present | حضور کامل</code>\n\n"
            "برای انصراف، «❌ انصراف» را بزنید."
        )
    await callback.message.answer(text, reply_markup=cancel_kb())
    await callback.answer()


@router.message(PollCreateForm.question, F.text)
async def poll_question(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if message.text == "❌ انصراف":
        await state.clear()
        user = await get_user(session, message.from_user, private=message.chat.type == "private")
        await message.answer("عملیات لغو شد.", reply_markup=await menu_for(session, user))
        return
    await state.update_data(poll_question=message.text.strip()[:500])
    await state.set_state(PollCreateForm.options)
    await message.answer("گزینه‌ها را در یک پیام و با علامت | جدا کنید؛ حداقل دو گزینه:", reply_markup=cancel_kb())


@router.message(PollCreateForm.options, F.text)
async def poll_options_submit(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if message.text == "❌ انصراف":
        await state.clear()
        user = await get_user(session, message.from_user, private=message.chat.type == "private")
        await message.answer("عملیات لغو شد.", reply_markup=await menu_for(session, user))
        return
    options = [value.strip()[:180] for value in message.text.split("|") if value.strip()]
    data = await state.get_data()
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    if len(options) < 2 or not data.get("evaluation_class_id") or not await is_class_manager(session, user, data["evaluation_class_id"]):
        await message.answer("حداقل دو گزینه وارد کنید و مطمئن شوید مدیر کلاس هستید.")
        return
    poll = Poll(class_id=data["evaluation_class_id"], question=data["poll_question"], created_by=user.id)
    session.add(poll)
    await session.flush()
    for position, label in enumerate(options):
        session.add(PollOption(poll_id=poll.id, label=label, position=position))
    await session.flush()
    await session.commit()
    await state.clear()
    await message.answer("✅ نظرسنجی با موفقیت ایجاد شد.", reply_markup=await menu_for(session, user))


@router.message(EvaluationForm.data, F.text)
async def evaluation_submit(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if message.text == "❌ انصراف":
        await state.clear()
        user = await get_user(session, message.from_user, private=message.chat.type == "private")
        await message.answer("عملیات لغو شد.", reply_markup=await menu_for(session, user))
        return
    data = await state.get_data()
    user = await get_user(session, message.from_user, private=message.chat.type == "private")
    class_id = data.get("evaluation_class_id")
    if not class_id or not await is_class_manager(session, user, class_id):
        await state.clear()
        await message.answer("دسترسی ثبت ارزیابی برای شما فعال نیست.", reply_markup=await menu_for(session, user))
        return
    parts = [part.strip() for part in message.text.split("|")]
    try:
        student_id = int(parts[0])
        membership = await session.scalar(select(ClassMembership.id).where(
            ClassMembership.class_id == class_id, ClassMembership.user_id == student_id,
            ClassMembership.is_active.is_(True)))
        student = await session.scalar(select(User.id).where(User.id == student_id, User.is_active.is_(True)))
        if membership is None or student is None:
            raise ValueError("دانش‌آموز فعال عضو این کلاس نیست.")
        if data["evaluation_kind"] == "attendance":
            if len(parts) < 2 or parts[1] not in {"present", "absent", "late", "excused"}:
                raise ValueError("وضعیت باید یکی از present، absent، late یا excused باشد.")
            class_context = await get_class_with_school(session, class_id)
            if class_context is None:
                raise ValueError("کلاس انتخاب‌شده در دسترس نیست.")
            local_today = datetime.now(ZoneInfo(class_context[2].timezone)).date()
            await save_attendance(
                session,
                class_id=class_id,
                student_id=student_id,
                attendance_date=local_today,
                status=parts[1],
                note=" | ".join(parts[2:]) or None,
                marked_by=user.id,
            )
            result = "حضور‌وغیاب امروز ثبت شد."
        else:
            if len(parts) < 4:
                raise ValueError("برای ثبت نمره حداقل چهار بخش لازم است.")
            subject_id, score, max_score = int(parts[1]), float(parts[2]), float(parts[3])
            validate_grade_values(score, max_score)
            linked_subject = await session.scalar(select(ClassSubject.id).where(
                ClassSubject.class_id == class_id,
                ClassSubject.subject_id == subject_id,
            ))
            if linked_subject is None:
                raise ValueError("درس انتخاب‌شده متعلق به این کلاس نیست.")
            session.add(Grade(class_id=class_id, student_id=student_id, subject_id=subject_id, source_type="manual", score=score, max_score=max_score, feedback=" | ".join(parts[4:]) or None, graded_by=user.id))
            result = "نمره و بازخورد با موفقیت ثبت شد."
        await session.flush()
    except (ValueError, TypeError) as exc:
        await message.answer(f"⚠️ {exc}\n\nلطفاً قالب را دقیق رعایت کنید.")
        return
    await session.commit()
    await state.clear()
    await message.answer(f"✅ {result}", reply_markup=await menu_for(session, user))
