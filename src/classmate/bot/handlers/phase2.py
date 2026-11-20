"""Phase-two learning workflows and support surface."""

import logging
from datetime import date, datetime, timezone
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.bot.handlers.ui import classes_for_user, get_user, menu_for
from classmate.bot.keyboards import cancel_kb, class_picker
from classmate.bot.states import EvaluationForm, PollCreateForm, ReviewSubmissionForm, SubmissionForm
from classmate.core.enums import Role
from classmate.db.models import Attendance, ClassMembership, Grade, Homework, HomeworkSubmission, Poll, PollOption, Subject, User
from classmate.domain.dates import jalali_date
from classmate.services.permissions import is_class_manager, user_can_access_class
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


def color_button(prefix: str, text: str, callback: str) -> InlineKeyboardButton:
    """Telegram has no button-color API; prefixes provide consistent semantic color coding."""
    return InlineKeyboardButton(text=f"{prefix} {text}", callback_data=callback)


@router.message(F.text.in_({"📤 ارسال تکلیف", "ارسال تکلیف"}))
async def submit_entry(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user)
    classes = await classes_for_user(session, user)
    if not classes:
        await message.answer("ابتدا با کد دعوت عضو یک کلاس شوید.")
    elif len(classes) == 1:
        await _show_homeworks(message, session, classes[0][0])
    else:
        await message.answer("🏫 کلاس موردنظر را انتخاب کنید:", reply_markup=class_picker(classes, action="submissions"))


async def _show_homeworks(message: Message, session: AsyncSession, class_id: int) -> None:
    rows = await approved_homeworks_for_student(session, class_id)
    if not rows:
        await message.answer("📚 در این کلاس تکلیف تأییدشده‌ای برای ارسال پاسخ وجود ندارد.")
        return
    buttons = []
    lines = ["📤 <b>ارسال پاسخ تکلیف</b>", "تکلیف موردنظر را انتخاب کنید:"]
    for homework, subject in rows:
        due = jalali_date(homework.due_on) if homework.due_on else "بدون موعد"
        lines.append(f"\n• {escape(subject.name)} — {escape(homework.title)} | موعد: {due}")
        buttons.append([color_button("🔵", f"{subject.name}: {homework.title[:30]}", f"p2:submit:{homework.id}")])
    buttons.append([color_button("🔴", "انصراف", "p2:cancel")])
    await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("p2:submit:"))
async def submit_homework(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    homework_id = int(callback.data.split(":")[2])
    homework = await session.get(Homework, homework_id)
    user = await get_user(session, callback.from_user)
    if homework is None or not await user_can_access_class(session, user, homework.class_id):
        await callback.answer("این تکلیف برای شما در دسترس نیست.", show_alert=True)
        return
    await state.update_data(homework_id=homework_id)
    await state.set_state(SubmissionForm.answer)
    await callback.message.answer("پاسخ خود را ارسال کنید؛ متن، فایل یا تصویر قابل قبول است.", reply_markup=cancel_kb())
    await callback.answer()


@router.message(SubmissionForm.answer, F.text)
async def submit_text(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if message.text == "❌ انصراف":
        await state.clear()
        return
    await _save_answer(message, state, session, text=message.text)


@router.message(SubmissionForm.answer, F.document)
async def submit_document(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await _save_answer(message, state, session, file_id=message.document.file_id, file_type="document")


@router.message(SubmissionForm.answer, F.photo)
async def submit_photo(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await _save_answer(message, state, session, file_id=message.photo[-1].file_id, file_type="photo")


async def _save_answer(message: Message, state: FSMContext, session: AsyncSession, text: str | None = None, file_id: str | None = None, file_type: str | None = None) -> None:
    data = await state.get_data()
    user = await get_user(session, message.from_user)
    try:
        await save_submission(session, data["homework_id"], user.id, text=text, file_id=file_id, file_type=file_type)
    except ValueError as exc:
        await state.clear()
        await message.answer(str(exc), reply_markup=await menu_for(session, user))
        return
    await state.clear()
    await message.answer("✅ پاسخ شما با موفقیت ثبت شد و برای بررسی دبیر ارسال گردید.", reply_markup=await menu_for(session, user))


@router.message(F.text.in_({"📊 نمرات من", "نمرات"}))
async def my_grades(message: Message, session: AsyncSession) -> None:
    user = await get_user(session, message.from_user)
    rows = await student_grades(session, user.id)
    if not rows:
        await message.answer("📊 هنوز نمره‌ای برای شما ثبت نشده است.")
        return
    lines = ["📊 <b>نمرات من</b>"]
    total = 0.0
    for grade, subject in rows:
        total += grade.score / grade.max_score * 20 if grade.max_score else 0
        note = f" — {escape(grade.feedback)}" if grade.feedback else ""
        lines.append(f"\n• {escape(subject.name)}: <b>{grade.score:g}</b> از {grade.max_score:g}{note}")
    average = total / len(rows)
    lines.append(f"\n📈 میانگین تقریبی: <b>{average:.2f}</b> از ۲۰")
    await message.answer("\n".join(lines))


@router.message(F.text.in_({"🟢 حضور من", "حضور"}))
async def my_attendance(message: Message, session: AsyncSession) -> None:
    user = await get_user(session, message.from_user)
    classes = await classes_for_user(session, user)
    rows = await student_attendance(session, user.id, [row[0] for row in classes])
    if not rows:
        await message.answer("🟢 هنوز سابقه حضور‌وغیابی برای شما ثبت نشده است.")
        return
    labels = {"present": "✅ حاضر", "absent": "🔴 غایب", "late": "🟡 تأخیر", "excused": "🔵 موجه"}
    counts = {key: sum(row.status == key for row in rows) for key in labels}
    lines = ["🟢 <b>گزارش حضور‌وغیاب من</b>", ""]
    lines.extend(f"{labels[key]}: {counts[key]}" for key in labels)
    lines.append("\n<b>آخرین وضعیت‌ها</b>")
    lines.extend(f"• {jalali_date(row.attendance_date)} — {labels.get(row.status, row.status)}" for row in rows[:10])
    await message.answer("\n".join(lines))


@router.message(F.text.in_({"🗳 نظرسنجی‌ها", "نظرسنجی"}))
async def polls(message: Message, session: AsyncSession) -> None:
    user = await get_user(session, message.from_user)
    classes = await classes_for_user(session, user)
    rows = await active_polls(session, [row[0] for row in classes])
    if not rows:
        await message.answer("🗳 در حال حاضر نظرسنجی فعالی برای شما وجود ندارد.")
        return
    buttons = [[color_button("🔵", poll.question[:45], f"p2:poll:{poll.id}")] for poll in rows]
    await message.answer("🗳 <b>نظرسنجی‌های فعال</b>\n\nبرای شرکت، یک مورد را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("p2:poll:"))
async def poll_open(callback: CallbackQuery, session: AsyncSession) -> None:
    poll_id = int(callback.data.split(":")[2])
    options = await poll_options(session, poll_id)
    if not options:
        await callback.answer("گزینه‌ای برای این نظرسنجی ثبت نشده است.", show_alert=True)
        return
    buttons = [[color_button("🟢", option.label, f"p2:vote:{poll_id}:{option.id}")] for option in options]
    buttons.append([color_button("🔴", "بستن", "p2:cancel")])
    poll = await session.get(Poll, poll_id)
    await callback.message.answer(f"🗳 <b>{escape(poll.question if poll else 'نظرسنجی')}</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@router.callback_query(F.data.startswith("p2:vote:"))
async def poll_vote(callback: CallbackQuery, session: AsyncSession) -> None:
    _, _, poll_id_s, option_id_s = callback.data.split(":")
    user = await get_user(session, callback.from_user)
    ok = await answer_poll(session, int(poll_id_s), user.id, int(option_id_s))
    await callback.answer("✅ رأی شما ثبت شد." if ok else "این نظرسنجی دیگر فعال نیست.", show_alert=True)


@router.callback_query(F.data == "p2:cancel")
async def phase2_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("عملیات لغو شد.")


@router.message(F.text.in_({"🆘 پشتیبانی", "پشتیبانی"}))
async def support(message: Message, session: AsyncSession) -> None:
    user = await get_user(session, message.from_user)
    await message.answer(
        "🆘 <b>مرکز پشتیبانی ClassMate 2</b>\n\n"
        "برای گزارش خطا، پیشنهاد قابلیت یا دریافت راهنمایی، با سازنده ربات در ارتباط باشید:\n\n"
        "👤 سازنده: <b>@Mizanup</b>\n"
        "📌 لطفاً هنگام ارسال مشکل، نام کلاس و شرح کوتاه مسئله را هم بنویسید.",
        reply_markup=await menu_for(session, user),
    )


@router.message(F.text == "👨‍🏫 ارزیابی کلاس")
async def manager_evaluation(message: Message, session: AsyncSession) -> None:
    user = await get_user(session, message.from_user)
    classes = [row for row in await classes_for_user(session, user) if await is_class_manager(session, user, row[0])]
    if not classes:
        await message.answer("شما مدیر هیچ کلاسی نیستید.")
        return
    await message.answer("🏫 کلاس موردنظر برای ارزیابی را انتخاب کنید:", reply_markup=class_picker(classes, action="evaluation"))


@router.message(F.text == "📝 بررسی پاسخ‌ها")
async def review_entry(message: Message, session: AsyncSession) -> None:
    user = await get_user(session, message.from_user)
    classes = [row for row in await classes_for_user(session, user) if await is_class_manager(session, user, row[0])]
    if not classes:
        await message.answer("شما مدیر هیچ کلاسی نیستید.")
        return
    await message.answer("🏫 کلاس موردنظر را انتخاب کنید:", reply_markup=class_picker(classes, action="review_submissions"))


async def submissions_panel(message: Message, session: AsyncSession, class_id: int) -> None:
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
    await message.answer("📝 <b>پاسخ‌های ارسالی</b>\n\nبرای بررسی و ثبت بازخورد یک مورد را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("p2:review:"))
async def review_open(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    submission = await session.get(HomeworkSubmission, int(callback.data.split(":")[2]))
    user = await get_user(session, callback.from_user)
    if submission is None:
        await callback.answer("پاسخ موردنظر پیدا نشد.", show_alert=True)
        return
    homework = await session.get(Homework, submission.homework_id)
    if homework is None or not await is_class_manager(session, user, homework.class_id):
        await callback.answer("دسترسی بررسی این پاسخ را ندارید.", show_alert=True)
        return
    content = submission.text or (f"فایل {submission.file_type} دریافت شده است." if submission.file_id else "بدون محتوای متنی")
    await callback.message.answer(f"📝 <b>{escape(homework.title)}</b>\n\n{escape(content)}\n\nبازخورد مدیر را ارسال کنید:", reply_markup=cancel_kb())
    await state.update_data(review_submission_id=submission.id)
    await state.set_state(ReviewSubmissionForm.feedback)
    await callback.answer()


@router.message(ReviewSubmissionForm.feedback, F.text)
async def review_submit(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if message.text == "❌ انصراف":
        await state.clear()
        return
    user = await get_user(session, message.from_user)
    data = await state.get_data()
    submission = await session.get(HomeworkSubmission, data["review_submission_id"])
    if submission is None:
        await state.clear()
        await message.answer("پاسخ موردنظر پیدا نشد.")
        return
    homework = await session.get(Homework, submission.homework_id)
    if homework is None or not await is_class_manager(session, user, homework.class_id):
        await state.clear()
        await message.answer("دسترسی بررسی این پاسخ را ندارید.")
        return
    submission.feedback = message.text.strip()
    submission.status = "reviewed"
    submission.reviewed_by = user.id
    submission.reviewed_at = datetime.now(timezone.utc)
    await session.flush()
    student = await session.get(User, submission.student_id)
    if student and student.private_chat_started:
        try:
            await message.bot.send_message(student.telegram_id, f"📝 بازخورد تکلیف «{escape(homework.title)}»:\n{escape(submission.feedback)}")
        except Exception:
            logger.warning("Could not send submission feedback to student", exc_info=True)
    await state.clear()
    await message.answer("✅ بازخورد ثبت و برای دانش‌آموز ارسال شد.", reply_markup=await menu_for(session, user))


async def evaluation_panel(message: Message, session: AsyncSession, state: FSMContext, class_id: int) -> None:
    await state.update_data(evaluation_class_id=class_id)
    await message.answer(
        "👨‍🏫 <b>ارزیابی کلاس</b>\n\n"
        "نوع عملیات را انتخاب کنید. برای حفظ دقت اطلاعات، شناسه دانش‌آموز و درس از بخش «کلاس‌های من» قابل مشاهده است.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [color_button("🟢", "ثبت حضور‌وغیاب", "p2:evaluate:attendance")],
            [color_button("🟡", "ثبت نمره و بازخورد", "p2:evaluate:grade")],
            [color_button("🔵", "ایجاد نظرسنجی", "p2:evaluate:poll")],
            [color_button("🔴", "انصراف", "p2:cancel")],
        ]),
    )


@router.callback_query(F.data.startswith("p2:evaluate:"))
async def evaluation_kind(callback: CallbackQuery, state: FSMContext) -> None:
    kind = callback.data.split(":")[2]
    await state.update_data(evaluation_kind=kind)
    await state.set_state(EvaluationForm.data)
    if kind == "grade":
        text = "قالب ثبت نمره:\nشناسه دانش‌آموز | شناسه درس | نمره | سقف نمره | بازخورد اختیاری"
    elif kind == "poll":
        await state.set_state(PollCreateForm.question)
        await callback.message.answer("سؤال نظرسنجی را ارسال کنید:", reply_markup=cancel_kb())
        await callback.answer()
        return
    else:
        text = "قالب حضور‌وغیاب:\nشناسه دانش‌آموز | وضعیت (present/absent/late/excused) | توضیح اختیاری"
    await callback.message.answer(text, reply_markup=cancel_kb())
    await callback.answer()


@router.message(PollCreateForm.question, F.text)
async def poll_question(message: Message, state: FSMContext) -> None:
    if message.text == "❌ انصراف":
        await state.clear()
        return
    await state.update_data(poll_question=message.text.strip()[:500])
    await state.set_state(PollCreateForm.options)
    await message.answer("گزینه‌ها را در یک پیام و با علامت | جدا کنید؛ حداقل دو گزینه:", reply_markup=cancel_kb())


@router.message(PollCreateForm.options, F.text)
async def poll_options_submit(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if message.text == "❌ انصراف":
        await state.clear()
        return
    options = [value.strip()[:180] for value in message.text.split("|") if value.strip()]
    data = await state.get_data()
    user = await get_user(session, message.from_user)
    if len(options) < 2 or not data.get("evaluation_class_id") or not await is_class_manager(session, user, data["evaluation_class_id"]):
        await message.answer("حداقل دو گزینه وارد کنید و مطمئن شوید مدیر کلاس هستید.")
        return
    poll = Poll(class_id=data["evaluation_class_id"], question=data["poll_question"], created_by=user.id)
    session.add(poll)
    await session.flush()
    for position, label in enumerate(options):
        session.add(PollOption(poll_id=poll.id, label=label, position=position))
    await session.flush()
    await state.clear()
    await message.answer("✅ نظرسنجی با موفقیت ایجاد شد.", reply_markup=await menu_for(session, user))


@router.message(EvaluationForm.data, F.text)
async def evaluation_submit(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if message.text == "❌ انصراف":
        await state.clear()
        return
    data = await state.get_data()
    user = await get_user(session, message.from_user)
    class_id = data.get("evaluation_class_id")
    if not class_id or not await is_class_manager(session, user, class_id):
        await state.clear()
        await message.answer("دسترسی ثبت ارزیابی برای شما فعال نیست.", reply_markup=await menu_for(session, user))
        return
    parts = [part.strip() for part in message.text.split("|")]
    try:
        student_id = int(parts[0])
        membership = await session.scalar(select(ClassMembership).where(
            ClassMembership.class_id == class_id, ClassMembership.user_id == student_id,
            ClassMembership.is_active.is_(True)))
        if membership is None:
            raise ValueError("دانش‌آموز عضو این کلاس نیست.")
        if data["evaluation_kind"] == "attendance":
            if len(parts) < 2 or parts[1] not in {"present", "absent", "late", "excused"}:
                raise ValueError("وضعیت باید یکی از present، absent، late یا excused باشد.")
            existing = await session.scalar(select(Attendance).where(
                Attendance.class_id == class_id, Attendance.student_id == student_id,
                Attendance.attendance_date == date.today()))
            if existing:
                existing.status, existing.note, existing.marked_by = parts[1], " | ".join(parts[2:]) or None, user.id
            else:
                session.add(Attendance(class_id=class_id, student_id=student_id, attendance_date=date.today(), status=parts[1], note=" | ".join(parts[2:]) or None, marked_by=user.id))
            result = "حضور‌وغیاب امروز ثبت شد."
        else:
            if len(parts) < 4:
                raise ValueError("برای ثبت نمره حداقل چهار بخش لازم است.")
            subject_id, score, max_score = int(parts[1]), float(parts[2]), float(parts[3])
            if score < 0 or max_score <= 0 or score > max_score:
                raise ValueError("مقدار نمره معتبر نیست.")
            subject = await session.get(Subject, subject_id)
            if subject is None:
                raise ValueError("درس موردنظر پیدا نشد.")
            session.add(Grade(class_id=class_id, student_id=student_id, subject_id=subject_id, source_type="manual", score=score, max_score=max_score, feedback=" | ".join(parts[4:]) or None, graded_by=user.id))
            result = "نمره و بازخورد با موفقیت ثبت شد."
        await session.flush()
    except (ValueError, TypeError) as exc:
        await message.answer(f"⚠️ {exc}\n\nلطفاً قالب را دقیق رعایت کنید.")
        return
    await state.clear()
    await message.answer(f"✅ {result}", reply_markup=await menu_for(session, user))
