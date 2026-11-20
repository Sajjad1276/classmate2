"""Phase-one product features exposed through the button-first UX."""

from datetime import date
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.bot.handlers.ui import classes_for_user, get_user, menu_for, render_panel
from classmate.bot.keyboards import cancel_kb, class_picker
from classmate.bot.states import AnnouncementForm, EditRecordForm, SearchForm
from classmate.domain.dates import jalali_date, jalali_datetime, persian_weekday
from classmate.core.enums import RecordStatus
from classmate.db.models import ClassGroup, DailyReport, Exam, Homework, Subject
from classmate.services.permissions import is_class_manager
from classmate.services.phase1 import (
    calendar_rows,
    create_announcement,
    dashboard_data,
    get_preferences,
    list_announcements,
    search_records,
    archive_record,
    update_record,
)

router = Router(name="phase1")
WEEKDAYS = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]


def dashboard_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 تقویم ۷ روز آینده", callback_data="p1:calendar")],
        [InlineKeyboardButton(text="📣 اطلاعیه‌ها", callback_data="p1:announcements")],
        [InlineKeyboardButton(text="🔄 به‌روزرسانی داشبورد", callback_data="p1:dashboard")],
    ])


def announcement_kb(can_create: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if can_create:
        rows.append([InlineKeyboardButton(text="➕ ایجاد اطلاعیه", callback_data="p1:new_announcement")])
    rows.append([InlineKeyboardButton(text="« بازگشت به داشبورد", callback_data="p1:dashboard")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_dashboard(message: Message, session: AsyncSession, user) -> None:
    classes = await classes_for_user(session, user)
    data = await dashboard_data(session, [row[0] for row in classes], date.today())
    lines = ["🏠 <b>داشبورد ClassMate</b>", "", "خلاصه فعالیت‌های آموزشی شما در روزهای پیش‌رو:"]
    if data["homework"]:
        lines.append(f"\n📚 <b>تکالیف نزدیک</b> ({len(data['homework'])})")
        for hw, subject, cls in data["homework"][:5]:
            due = jalali_date(hw.due_on) if hw.due_on else "بدون موعد"
            lines.append(f"• {escape(subject.name)} — {escape(hw.title)} | {due} | {escape(cls.name)}")
    else:
        lines.append("\n📚 تکلیف نزدیک به موعدی ندارید.")
    if data["exams"]:
        lines.append(f"\n📝 <b>امتحانات پیش‌رو</b> ({len(data['exams'])})")
        for exam, subject, cls in data["exams"][:5]:
            lines.append(f"• {escape(subject.name)} — {escape(exam.title)} | {jalali_datetime(exam.scheduled_at, 'Asia/Tehran')} | {escape(cls.name)}")
    else:
        lines.append("\n📝 امتحان پیش‌رویی ثبت نشده است.")
    if data["announcements"]:
        lines.append(f"\n📣 <b>آخرین اطلاعیه‌ها</b> ({len(data['announcements'])})")
        for item, cls in data["announcements"][:3]:
            lines.append(f"• {escape(item.title)} — {escape(cls.name)}")
    await render_panel(message, "\n".join(lines), dashboard_kb())


@router.message(F.text.in_({"🏠 داشبورد من", "داشبورد من"}))
async def dashboard(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user)
    await _render_dashboard(message, session, user)


@router.callback_query(F.data == "p1:dashboard")
async def dashboard_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    user = await get_user(session, callback.from_user)
    await _render_dashboard(callback.message, session, user)
    await callback.answer("داشبورد به‌روزرسانی شد.")


@router.callback_query(F.data == "p1:calendar")
async def calendar_entry(callback: CallbackQuery, session: AsyncSession) -> None:
    user = await get_user(session, callback.from_user)
    classes = await classes_for_user(session, user)
    if not classes:
        await callback.message.answer("هنوز عضو کلاسی نیستید.")
    elif len(classes) == 1:
        await _render_calendar(callback.message, session, classes[0][0], classes[0][1])
    else:
        await callback.message.answer("🏫 کلاس موردنظر برای مشاهده تقویم را انتخاب کنید:", reply_markup=class_picker(classes, action="calendar"))
    await callback.answer()


async def _render_calendar(message: Message, session: AsyncSession, class_id: int, class_name: str) -> None:
    timetable, exams, homeworks = await calendar_rows(session, class_id, date.today())
    lines = [f"📅 <b>تقویم آموزشی — {escape(class_name)}</b>", "", "رویدادهای هفت روز آینده:"]
    if exams:
        lines.append("\n📝 <b>امتحانات</b>")
        for exam, subject in exams:
            lines.append(f"• {jalali_datetime(exam.scheduled_at, 'Asia/Tehran')} — {escape(subject.name)}: {escape(exam.title)}")
    if homeworks:
        lines.append("\n📚 <b>موعد تکالیف</b>")
        for hw, subject in homeworks:
            lines.append(f"• {jalali_date(hw.due_on)} — {escape(subject.name)}: {escape(hw.title)}")
    today_weekday = persian_weekday(date.today())
    todays = [(entry, subject) for entry, subject in timetable if entry.weekday == today_weekday]
    if todays:
        lines.append("\n🏫 <b>کلاس‌های امروز</b>")
        for entry, subject in todays:
            time = entry.starts_at.strftime('%H:%M') if entry.starts_at else "زمان نامشخص"
            lines.append(f"• زنگ {entry.period} — {escape(subject.name)} ({time})")
    if not exams and not homeworks and not todays:
        lines.append("\nبرای این بازه رویداد آموزشی ثبت نشده است.")
    await render_panel(message, "\n".join(lines), dashboard_kb())


@router.message(F.text.in_({"🔎 جست‌وجو", "جست‌وجو"}))
async def search_entry(message: Message, state: FSMContext) -> None:
    if message.chat.type != "private":
        return
    await state.set_state(SearchForm.query)
    await message.answer("🔎 عبارت جست‌وجو را وارد کنید؛ مانند نام درس، تکلیف یا امتحان:", reply_markup=cancel_kb())


@router.message(SearchForm.query, F.text)
async def search_submit(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if message.text == "❌ انصراف":
        return
    user = await get_user(session, message.from_user)
    classes = await classes_for_user(session, user)
    rows = await search_records(session, [row[0] for row in classes], message.text)
    await state.clear()
    if not rows:
        await message.answer("نتیجه‌ای برای جست‌وجوی شما پیدا نشد.", reply_markup=await menu_for(session, user))
        return
    lines = [f"🔎 <b>نتایج جست‌وجو برای «{escape(message.text.strip())}»</b>"]
    for kind, row in rows[:20]:
        if kind == "homework":
            item, subject, cls = row
            lines.append(f"\n📚 {escape(subject.name)} — {escape(item.title)} | {escape(cls.name)}")
        elif kind == "exam":
            item, subject, cls = row
            lines.append(f"\n📝 {escape(subject.name)} — {escape(item.title)} | {escape(cls.name)}")
        else:
            item, cls = row
            lines.append(f"\n📋 {escape(item.title or 'گزارش روزانه')} | {escape(cls.name)}")
    await message.answer("\n".join(lines), reply_markup=await menu_for(session, user))


@router.message(F.text.in_({"🔔 تنظیم اعلان‌ها", "اعلان‌ها", "تنظیم اعلان‌ها"}))
async def notification_settings(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user)
    classes = await classes_for_user(session, user)
    if not classes:
        await message.answer("برای تنظیم اعلان‌ها ابتدا عضو یک کلاس شوید.")
        return
    if len(classes) > 1:
        await message.answer("🏫 کلاس موردنظر را انتخاب کنید:", reply_markup=class_picker(classes, action="prefs"))
        return
    await _render_preferences(message, session, user, classes[0][0], classes[0][1])


async def _render_preferences(message: Message, session: AsyncSession, user, class_id: int, class_name: str) -> None:
    pref = await get_preferences(session, user.id, class_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📚 تکالیف: {'فعال' if pref.homework_enabled else 'خاموش'}", callback_data=f"p1:toggle:hw:{class_id}" )],
        [InlineKeyboardButton(text=f"📝 امتحانات: {'فعال' if pref.exams_enabled else 'خاموش'}", callback_data=f"p1:toggle:exam:{class_id}" )],
        [InlineKeyboardButton(text="🏠 بازگشت", callback_data="p1:dashboard")],
    ])
    await message.answer(f"🔔 <b>تنظیم اعلان‌ها — {escape(class_name)}</b>\n\nاعلان‌های موردنظر را فعال یا خاموش کنید.", reply_markup=kb)


@router.callback_query(F.data.startswith("p1:toggle:"))
async def toggle_preference(callback: CallbackQuery, session: AsyncSession) -> None:
    _, _, kind, class_id_s = callback.data.split(":")
    user = await get_user(session, callback.from_user)
    class_id = int(class_id_s)
    pref = await get_preferences(session, user.id, class_id)
    if kind == "hw":
        pref.homework_enabled = not pref.homework_enabled
    else:
        pref.exams_enabled = not pref.exams_enabled
    await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📚 تکالیف: {'فعال' if pref.homework_enabled else 'خاموش'}", callback_data=f"p1:toggle:hw:{class_id}")],
        [InlineKeyboardButton(text=f"📝 امتحانات: {'فعال' if pref.exams_enabled else 'خاموش'}", callback_data=f"p1:toggle:exam:{class_id}")],
        [InlineKeyboardButton(text="🏠 بازگشت", callback_data="p1:dashboard")],
    ]))
    await callback.answer("تنظیم اعلان‌ها به‌روزرسانی شد.")


@router.message(F.text.in_({"📣 اطلاعیه‌ها", "اطلاعیه‌ها"}))
async def announcements(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user)
    classes = await classes_for_user(session, user)
    rows = await list_announcements(session, [row[0] for row in classes])
    manager = False
    for row in classes:
        if await is_class_manager(session, user, row[0]):
            manager = True
            break
    if not rows:
        text = "📣 <b>اطلاعیه‌ها</b>\n\nاطلاعیه فعالی برای کلاس‌های شما وجود ندارد."
    else:
        parts = ["📣 <b>آخرین اطلاعیه‌ها</b>"]
        for item, cls in rows:
            parts.append(f"\n<b>{escape(item.title)}</b> — {escape(cls.name)}\n{escape(item.body)}")
        text = "\n".join(parts)
    await message.answer(text, reply_markup=announcement_kb(manager))


@router.message(F.text.in_({"🗂 مدیریت محتوا", "مدیریت محتوا"}))
async def content_management(message: Message, session: AsyncSession) -> None:
    if message.chat.type != "private":
        return
    user = await get_user(session, message.from_user)
    classes = [row for row in await classes_for_user(session, user) if await is_class_manager(session, user, row[0])]
    if not classes:
        await message.answer("شما مدیر هیچ کلاسی نیستید.")
        return
    await message.answer("🏫 کلاس موردنظر برای مدیریت محتوا را انتخاب کنید:", reply_markup=class_picker(classes, action="content"))


async def _render_content(message: Message, session: AsyncSession, class_id: int) -> None:
    rows = []
    for kind, model in (("homework", Homework), ("exam", Exam), ("report", DailyReport)):
        records = (await session.execute(
            select(model).where(model.class_id == class_id, model.status != RecordStatus.CANCELLED)
            .order_by(model.created_at.desc()).limit(10)
        )).scalars().all()
        rows.extend((kind, item) for item in records)
    if not rows:
        await message.answer("رکورد فعالی برای مدیریت وجود ندارد.")
        return
    lines = ["🗂 <b>مدیریت محتوا</b>", "برای آرشیو هر مورد، دکمه مربوط را انتخاب کنید:"]
    buttons = []
    for kind, item in rows[:20]:
        title = getattr(item, "title", None) or "گزارش روزانه"
        status = "تأییدشده" if item.status == RecordStatus.APPROVED else "در انتظار بررسی"
        lines.append(f"\n• {escape(title)} — {status}")
        buttons.append([
            InlineKeyboardButton(text=f"✏️ ویرایش: {title[:18]}", callback_data=f"p1:edit:{kind}:{item.id}"),
            InlineKeyboardButton(text="🗃 آرشیو", callback_data=f"p1:archive:{kind}:{item.id}"),
        ])
    buttons.append([InlineKeyboardButton(text="🏠 داشبورد", callback_data="p1:dashboard")])
    await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("p1:archive:"))
async def archive_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    _, _, kind, record_id = callback.data.split(":")
    user = await get_user(session, callback.from_user)
    ok, text = await archive_record(session, kind, int(record_id), user)
    await callback.answer(text, show_alert=not ok)
    if ok:
        await callback.message.edit_text("✅ مورد انتخاب‌شده آرشیو شد. برای مشاهده موارد باقی‌مانده دوباره پنل مدیریت محتوا را باز کنید.")


@router.callback_query(F.data.startswith("p1:edit:"))
async def edit_callback(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    _, _, kind, record_id = callback.data.split(":")
    user = await get_user(session, callback.from_user)
    model = {"homework": Homework, "exam": Exam, "report": DailyReport}[kind]
    record = await session.get(model, int(record_id))
    if record is None or not await is_class_manager(session, user, record.class_id):
        await callback.answer("دسترسی ویرایش این مورد را ندارید.", show_alert=True)
        return
    await state.update_data(edit_kind=kind, edit_id=int(record_id))
    await state.set_state(EditRecordForm.first)
    await callback.message.answer("عنوان جدید را ارسال کنید:", reply_markup=cancel_kb())
    await callback.answer()


@router.message(EditRecordForm.first, F.text)
async def edit_first(message: Message, state: FSMContext) -> None:
    if message.text == "❌ انصراف":
        return
    data = await state.get_data()
    await state.update_data(first=message.text)
    await state.set_state(EditRecordForm.second)
    label = "توضیحات جدید" if data["edit_kind"] == "homework" else "محدوده جدید" if data["edit_kind"] == "exam" else "متن جدید گزارش"
    await message.answer(f"{label} را ارسال کنید:", reply_markup=cancel_kb())


@router.message(EditRecordForm.second, F.text)
async def edit_second(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if message.text == "❌ انصراف":
        return
    data = await state.get_data()
    user = await get_user(session, message.from_user)
    ok, text = await update_record(session, data["edit_kind"], data["edit_id"], data["first"], message.text, user)
    await state.clear()
    await message.answer(("✅ " if ok else "⚠️ ") + text, reply_markup=await menu_for(session, user))


@router.callback_query(F.data == "p1:announcements")
async def announcements_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    await announcements(callback.message, session)
    await callback.answer()


@router.callback_query(F.data == "p1:new_announcement")
async def new_announcement(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    user = await get_user(session, callback.from_user)
    classes = [row for row in await classes_for_user(session, user) if await is_class_manager(session, user, row[0])]
    if not classes:
        await callback.answer("دسترسی ایجاد اطلاعیه ندارید.", show_alert=True)
        return
    await callback.message.answer("🏫 کلاس اطلاعیه را انتخاب کنید:", reply_markup=class_picker(classes, action="announcement"))
    await callback.answer()


async def start_announcement_after_class(message, state: FSMContext, class_id: int) -> None:
    await state.update_data(class_id=class_id)
    await state.set_state(AnnouncementForm.title)
    await message.answer("عنوان اطلاعیه را ارسال کنید:", reply_markup=cancel_kb())


@router.message(AnnouncementForm.title, F.text)
async def announcement_title(message: Message, state: FSMContext) -> None:
    if message.text == "❌ انصراف":
        return
    await state.update_data(title=message.text.strip()[:180])
    await state.set_state(AnnouncementForm.body)
    await message.answer("متن اطلاعیه را ارسال کنید:", reply_markup=cancel_kb())


@router.message(AnnouncementForm.body, F.text)
async def announcement_body(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if message.text == "❌ انصراف":
        return
    user = await get_user(session, message.from_user)
    data = await state.get_data()
    try:
        item = await create_announcement(session, data["class_id"], data["title"], message.text, user)
    except PermissionError as exc:
        await state.clear()
        await message.answer(str(exc), reply_markup=await menu_for(session, user))
        return
    await state.clear()
    await message.answer(f"✅ اطلاعیه «{escape(item.title)}» با موفقیت منتشر شد.", reply_markup=await menu_for(session, user))
