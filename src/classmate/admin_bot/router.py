"""Telegram-native management control center for ClassMate 2."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.admin_bot.access import (
    ROLE_LABELS,
    admin_account,
    has_admin_permission,
    is_bootstrap_owner,
)
from classmate.admin_bot.services import (
    DEFAULT_DASHBOARD_CARDS,
    assign_subject,
    audit_action,
    create_admin_account,
    create_class,
    create_school,
    create_school_subject,
    dashboard_preference,
    dashboard_stats,
    deactivate_admin_account,
    disconnect_group,
    ensure_admin_user,
    issue_group_link_code,
    list_admin_accounts,
    list_class_content_queue,
    list_class_members,
    list_classes,
    list_recent_audit,
    list_schools,
    list_school_subjects,
    set_class_member_active,
    set_class_active,
    set_school_active,
    unassign_subject,
)
from classmate.core.config import get_settings
from classmate.db.models import (
    Announcement,
    ClassGroup,
    ClassMembership,
    ClassSubject,
    DailyReport,
    Exam,
    Homework,
    School,
    Subject,
    User,
)
from classmate.domain.dates import jalali_datetime
from classmate.services.outbox import enqueue_telegram_message
from classmate.services.records import approve_record, reject_record

router = Router(name="management-control-center")
logger = logging.getLogger(__name__)


class AdminWizard(StatesGroup):
    school_name = State()
    school_rename = State()
    class_name = State()
    class_grade = State()
    class_section = State()
    class_edit_name = State()
    class_edit_grade = State()
    subject_name = State()
    admin_telegram_id = State()
    admin_display_name = State()
    announcement_title = State()
    announcement_body = State()
    dashboard_setting = State()


HOME = "🏠 منوی مدیریت"
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 آمار و داشبورد"), KeyboardButton(text="🏫 مدارس")],
        [KeyboardButton(text="🏷 کلاس‌ها"), KeyboardButton(text="📚 دروس")],
        [KeyboardButton(text="👥 کاربران و اعضا"), KeyboardButton(text="🔗 گروه‌های کلاس")],
        [KeyboardButton(text="📥 صف بررسی"), KeyboardButton(text="📣 اطلاعیه‌ها")],
        [KeyboardButton(text="📈 گزارش و سوابق"), KeyboardButton(text="🛡 مدیران")],
        [KeyboardButton(text="⚙️ تنظیمات داشبورد")],
    ],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder="بخش مدیریتی را انتخاب کنید",
)


def panel_keyboard(
    rows: list[list[InlineKeyboardButton]],
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def home_row() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=HOME, callback_data="admin:home")]


async def _allowed(session: AsyncSession, user_id: int, permission: str) -> bool:
    return await has_admin_permission(session, user_id, permission)


async def _reject_message(message: Message) -> None:
    await message.answer("⛔ این ربات فقط برای مدیران مجاز سامانه در دسترس است.")


async def _reject_callback(callback: CallbackQuery) -> None:
    await callback.answer("دسترسی لازم را ندارید.", show_alert=True)


async def _ensure_actor(session: AsyncSession, user) -> User:
    settings = get_settings()
    bootstrap = user.id in settings.bootstrap_admin_id_set
    return await ensure_admin_user(session, user, bootstrap=bootstrap)


async def _panel(
    message: Message,
    text: str,
    rows: list[list[InlineKeyboardButton]] | None = None,
) -> None:
    markup = panel_keyboard((rows or []) + [home_row()])
    await message.answer(text, reply_markup=markup, disable_web_page_preview=True)


async def _edit_panel(
    callback: CallbackQuery,
    text: str,
    rows: list[list[InlineKeyboardButton]] | None = None,
) -> None:
    if callback.message is None:
        return
    markup = panel_keyboard((rows or []) + [home_row()])
    await callback.message.edit_text(
        text,
        reply_markup=markup,
        disable_web_page_preview=True,
    )


def _fmt_number(value: int | float) -> str:
    return f"{value:,}"


def _dashboard_text(stats: dict, visible: list[str]) -> str:
    sections: dict[str, list[str]] = {
        "users": [
            f"👥 کل کاربران: {_fmt_number(stats['users_total'])}",
            f"👨‍👦‍👦 کاربران فعال: {_fmt_number(stats['users_active'])}",
        ],
        "growth": [
            "📥 ثبت‌نام روز / هفته / ماه:",
            f"{_fmt_number(stats['users_1d'])} / {_fmt_number(stats['users_7d'])} / {_fmt_number(stats['users_30d'])}",
            "⏱ ثبت‌نام یک / چهار / دوازده ساعت گذشته:",
            f"{_fmt_number(stats['users_1h'])} / {_fmt_number(stats['users_4h'])} / {_fmt_number(stats['users_12h'])}",
        ],
        "schools": [
            f"🏫 مدارس: {_fmt_number(stats['schools_active'])} فعال از {_fmt_number(stats['schools_total'])}",
        ],
        "classes": [
            f"🏷 کلاس‌ها: {_fmt_number(stats['classes_active'])} فعال از {_fmt_number(stats['classes_total'])}",
        ],
        "groups": [
            f"🔗 گروه‌های متصل: {_fmt_number(stats['groups_connected'])}",
            f"⚠️ کلاس‌های بدون گروه: {_fmt_number(stats['groups_missing'])}",
        ],
        "content": [
            f"📚 تکلیف / امتحان / گزارش: {_fmt_number(stats['homeworks'])} / {_fmt_number(stats['exams'])} / {_fmt_number(stats['reports'])}",
            f"🕓 در انتظار بررسی: {_fmt_number(stats['pending_homework'] + stats['pending_exams'] + stats['pending_reports'])}",
            f"📝 حضورغیاب / نمره: {_fmt_number(stats['attendance'])} / {_fmt_number(stats['grades'])}",
        ],
        "files": [
            f"📂 فایل‌های ثبت‌شده در پاسخ تکالیف: {_fmt_number(stats['files'])}",
        ],
        "delivery": [
            f"📤 صف ارسال: {_fmt_number(stats['outbox_pending'])} در انتظار · {_fmt_number(stats['outbox_failed'])} ناموفق",
        ],
        "health": [
            f"🎛 زمان پاسخ پایگاه داده: {stats['database_ping_ms']} ms",
            "📡 بار سرور: " + (
                " / ".join(str(v) for v in stats["server_load"])
                if stats["server_load"] is not None
                else "N/A"
            ),
        ],
    }
    order = ["users", "growth", "schools", "classes", "groups", "content", "files", "delivery", "health"]
    lines = ["╔════════════════════════════╗", "║       🤖 کنترل‌سنتر ClassMate 2", "╠════════════════════════════╣"]
    for key in order:
        if key not in visible:
            continue
        lines.extend(f"║ {line}" for line in sections[key])
        lines.append("║")
    now = datetime.now(timezone.utc)
    lines.extend(
        [
            "╠════════════════════════════╣",
            f"║ 🗓 {jalali_datetime(now, 'Asia/Tehran')}",
            "╚════════════════════════════╝",
        ]
    )
    return "\n".join(lines)


@router.message(CommandStart())
async def admin_start(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if message.chat.type != "private":
        await message.answer("برای مدیریت، ربات را در گفت‌وگوی خصوصی باز کنید.")
        return
    if not await _allowed(session, message.from_user.id, "dashboard:read"):
        await _reject_message(message)
        return
    await state.clear()
    account = await admin_account(session, message.from_user.id)
    title = "مالک سامانه" if await is_bootstrap_owner(message.from_user.id) else ROLE_LABELS.get(account.role if account else "viewer", "مدیر")
    await message.answer(
        f"🤖 <b>مرکز مدیریت ClassMate 2</b>\n"
        f"سطح دسترسی: <b>{escape(title)}</b>\n\n"
        "از صفحه‌کلید اصلی بخش موردنظر را انتخاب کنید.",
        reply_markup=MAIN_KEYBOARD,
    )


@router.message(Command("home"))
@router.message(F.text == HOME)
async def admin_home_message(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not await _allowed(session, message.from_user.id, "dashboard:read"):
        await _reject_message(message)
        return
    await state.clear()
    await message.answer("🏠 <b>منوی مدیریت</b>", reply_markup=MAIN_KEYBOARD)


@router.callback_query(F.data == "admin:home")
async def admin_home_callback(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if not await _allowed(session, callback.from_user.id, "dashboard:read"):
        await _reject_callback(callback)
        return
    await state.clear()
    await callback.answer()
    if callback.message:
        await callback.message.answer("🏠 <b>منوی مدیریت</b>", reply_markup=MAIN_KEYBOARD)


async def show_dashboard(message: Message, session: AsyncSession, telegram_id: int) -> None:
    if not await _allowed(session, telegram_id, "dashboard:read"):
        await message.answer("دسترسی مشاهده داشبورد را ندارید.")
        return
    stats = await dashboard_stats(session)
    preference = await dashboard_preference(session, telegram_id)
    await _panel(
        message,
        _dashboard_text(stats, preference.visible_cards or DEFAULT_DASHBOARD_CARDS),
        [
            [InlineKeyboardButton(text="🔄 به‌روزرسانی آمار", callback_data="admin:dashboard")],
            [InlineKeyboardButton(text="⚙️ شخصی‌سازی نمایش", callback_data="admin:dashboard:settings")],
        ],
    )


@router.message(F.text == "📊 آمار و داشبورد")
async def dashboard_message(message: Message, session: AsyncSession) -> None:
    await show_dashboard(message, session, message.from_user.id)


@router.callback_query(F.data == "admin:dashboard")
async def dashboard_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "dashboard:read"):
        await _reject_callback(callback)
        return
    stats = await dashboard_stats(session)
    preference = await dashboard_preference(session, callback.from_user.id)
    await callback.answer("آمار به‌روزرسانی شد.")
    await _edit_panel(
        callback,
        _dashboard_text(stats, preference.visible_cards or DEFAULT_DASHBOARD_CARDS),
        [
            [InlineKeyboardButton(text="🔄 به‌روزرسانی آمار", callback_data="admin:dashboard")],
            [InlineKeyboardButton(text="⚙️ شخصی‌سازی نمایش", callback_data="admin:dashboard:settings")],
        ],
    )


@router.callback_query(F.data == "admin:dashboard:settings")
async def dashboard_settings_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "dashboard:read"):
        await _reject_callback(callback)
        return
    pref = await dashboard_preference(session, callback.from_user.id)
    labels = {
        "users": "کاربران",
        "growth": "رشد کاربران",
        "schools": "مدارس",
        "classes": "کلاس‌ها",
        "groups": "گروه‌ها",
        "content": "محتوا و صف بررسی",
        "files": "فایل‌ها",
        "delivery": "ارسال پیام",
        "health": "سلامت سرور",
    }
    rows = []
    for key in labels:
        mark = "✅" if key in pref.visible_cards else "▫️"
        rows.append(
            [InlineKeyboardButton(text=f"{mark} {labels[key]}", callback_data=f"admin:dashboard:toggle:{key}")]
        )
    await callback.answer()
    await _edit_panel(
        callback,
        "⚙️ <b>شخصی‌سازی داشبورد</b>\n"
        "کارت‌هایی را که در گزارش اصلی نمایش داده می‌شوند روشن یا خاموش کنید.",
        rows,
    )


@router.callback_query(F.data.startswith("admin:dashboard:toggle:"))
async def dashboard_toggle_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "dashboard:read"):
        await _reject_callback(callback)
        return
    key = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if key not in DEFAULT_DASHBOARD_CARDS:
        await callback.answer("گزینه نامعتبر است.", show_alert=True)
        return
    pref = await dashboard_preference(session, callback.from_user.id)
    visible = set(pref.visible_cards or DEFAULT_DASHBOARD_CARDS)
    if key in visible:
        if len(visible) == 1:
            await callback.answer("حداقل یک بخش باید روشن بماند.", show_alert=True)
            return
        visible.remove(key)
    else:
        visible.add(key)
    pref.visible_cards = [item for item in DEFAULT_DASHBOARD_CARDS if item in visible]
    await callback.answer("تنظیم ذخیره شد.")
    labels = {
        "users": "کاربران", "growth": "رشد کاربران", "schools": "مدارس",
        "classes": "کلاس‌ها", "groups": "گروه‌ها", "content": "محتوا و صف بررسی",
        "files": "فایل‌ها", "delivery": "ارسال پیام", "health": "سلامت سرور",
    }
    rows = [
        [InlineKeyboardButton(text=f"{'✅' if item in pref.visible_cards else '▫️'} {labels[item]}",
                              callback_data=f"admin:dashboard:toggle:{item}")]
        for item in DEFAULT_DASHBOARD_CARDS
    ]
    await _edit_panel(
        callback,
        "⚙️ <b>شخصی‌سازی داشبورد</b>\nکارت‌های موردنظر را روشن یا خاموش کنید.",
        rows,
    )


async def show_schools(message: Message, session: AsyncSession, actor: int, notice: str | None = None) -> None:
    if not await _allowed(session, actor, "schools:read"):
        await message.answer("دسترسی مشاهده مدارس را ندارید.")
        return
    schools = await list_schools(session)
    lines = ["🏫 <b>مدیریت مدارس</b>", ""]
    if notice:
        lines.extend([notice, ""])
    rows: list[list[InlineKeyboardButton]] = []
    for school in schools[:30]:
        status = "فعال" if school.is_active else "غیرفعال"
        lines.append(f"• {escape(school.name)} — {status} · #{school.id}")
        rows.append(
            [InlineKeyboardButton(text=f"مدیریت {school.name[:35]}", callback_data=f"admin:school:open:{school.id}")]
        )
    if not schools:
        lines.append("مدرسه‌ای ثبت نشده است.")
    rows.append([InlineKeyboardButton(text="➕ ایجاد مدرسه", callback_data="admin:school:create")])
    await _panel(message, "\n".join(lines), rows)


@router.message(F.text == "🏫 مدارس")
async def schools_message(message: Message, session: AsyncSession) -> None:
    await show_schools(message, session, message.from_user.id)


@router.callback_query(F.data == "admin:schools")
async def schools_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "schools:read"):
        await _reject_callback(callback)
        return
    schools = await list_schools(session)
    rows = [
        [InlineKeyboardButton(text=f"{s.name[:36]} · {'فعال' if s.is_active else 'غیرفعال'}", callback_data=f"admin:school:open:{s.id}")]
        for s in schools[:30]
    ]
    rows.append([InlineKeyboardButton(text="➕ ایجاد مدرسه", callback_data="admin:school:create")])
    await callback.answer()
    await _edit_panel(callback, "🏫 <b>مدیریت مدارس</b>", rows)


@router.callback_query(F.data == "admin:school:create")
async def school_create_start(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "schools:write"):
        await _reject_callback(callback)
        return
    await state.clear()
    await state.set_state(AdminWizard.school_name)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "➕ <b>ایجاد مدرسه</b>\n\nنام مدرسه را وارد کنید.\nمنطقه زمانی به‌صورت پیش‌فرض Asia/Tehran تنظیم می‌شود.",
            reply_markup=panel_keyboard([[InlineKeyboardButton(text="لغو", callback_data="admin:school:cancel")]]),
        )


@router.message(AdminWizard.school_name, F.text)
async def school_create_name(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not await _allowed(session, message.from_user.id, "schools:write"):
        await state.clear()
        await _reject_message(message)
        return
    try:
        school = await create_school(
            session, actor=message.from_user.id, name=message.text or "", timezone_name="Asia/Tehran"
        )
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await message.answer(
        f"✅ مدرسه «{escape(school.name)}» ساخته شد.\nشناسه: <code>{school.id}</code>",
        reply_markup=MAIN_KEYBOARD,
    )
    await show_schools(message, session, message.from_user.id)


@router.callback_query(F.data == "admin:school:cancel")
async def school_create_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("عملیات لغو شد.")
    if callback.message:
        await callback.message.edit_text("ایجاد مدرسه لغو شد.")


@router.callback_query(F.data.startswith("admin:school:open:"))
async def school_open(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "schools:read"):
        await _reject_callback(callback)
        return
    try:
        school_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
    except ValueError:
        await callback.answer("شناسه مدرسه معتبر نیست.", show_alert=True)
        return
    school = await session.get(School, school_id)
    if school is None:
        await callback.answer("مدرسه پیدا نشد.", show_alert=True)
        return
    classes = await list_classes(session, school_id=school_id)
    status = "فعال" if school.is_active else "غیرفعال"
    connected = sum(1 for cls, _ in classes if cls.group_chat_id is not None)
    body = (
        f"🏫 <b>{escape(school.name)}</b>\n"
        f"شناسه: {school.id}\n"
        f"وضعیت: {status}\n"
        f"منطقه زمانی: {escape(school.timezone)}\n"
        f"کلاس‌ها: {len(classes)} · گروه متصل: {connected}\n"
    )
    rows = [
        [InlineKeyboardButton(text="🏷 مشاهده کلاس‌ها", callback_data=f"admin:classes:school:{school_id}")],
        [InlineKeyboardButton(text="📚 مدیریت دروس", callback_data=f"admin:subjects:school:{school_id}")],
        [InlineKeyboardButton(text="✏️ تغییر نام", callback_data=f"admin:school:rename:{school_id}")],
        [
            InlineKeyboardButton(
                text="⏸ غیرفعال‌سازی" if school.is_active else "✅ فعال‌سازی",
                callback_data=f"admin:school:toggle:{school_id}",
            )
        ],
        [InlineKeyboardButton(text="⬅️ فهرست مدارس", callback_data="admin:schools")],
    ]
    await callback.answer()
    await _edit_panel(callback, body, rows)


@router.callback_query(F.data.startswith("admin:school:rename:"))
async def school_rename_start(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "schools:write"):
        await _reject_callback(callback)
        return
    try:
        school_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
    except ValueError:
        await callback.answer("شناسه مدرسه معتبر نیست.", show_alert=True)
        return
    if await session.get(School, school_id) is None:
        await callback.answer("مدرسه پیدا نشد.", show_alert=True)
        return
    await state.clear()
    await state.update_data(school_id=school_id)
    await state.set_state(AdminWizard.school_rename)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "نام جدید مدرسه را وارد کنید:",
            reply_markup=panel_keyboard([[InlineKeyboardButton(text="لغو", callback_data=f"admin:school:open:{school_id}")]]),
        )


@router.message(AdminWizard.school_rename, F.text)
async def school_rename_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not await _allowed(session, message.from_user.id, "schools:write"):
        await state.clear()
        await _reject_message(message)
        return
    data = await state.get_data()
    try:
        school = await rename_school(
            session, actor=message.from_user.id, school_id=int(data["school_id"]), name=message.text or ""
        )
    except (ValueError, KeyError) as exc:
        await message.answer(str(exc) or "مدرسه پیدا نشد.")
        return
    await state.clear()
    await message.answer(f"✅ نام مدرسه به «{escape(school.name)}» تغییر کرد.", reply_markup=MAIN_KEYBOARD)


@router.callback_query(F.data.startswith("admin:school:toggle:"))
async def school_toggle(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "schools:write"):
        await _reject_callback(callback)
        return
    try:
        school_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
        school = await session.get(School, school_id)
        if school is None:
            raise ValueError("مدرسه پیدا نشد.")
        school = await set_school_active(
            session, actor=callback.from_user.id, school_id=school_id, active=not school.is_active
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("وضعیت مدرسه تغییر کرد.")
    updated = await session.get(School, school_id)
    classes = await list_classes(session, school_id=school_id)
    connected = sum(1 for cls, _school in classes if cls.group_chat_id is not None)
    status = "فعال" if updated and updated.is_active else "غیرفعال"
    body = (
        f"🏫 <b>{escape(updated.name if updated else '')}</b>\\n"
        f"شناسه: {school_id}\\nوضعیت: {status}\\n"
        f"کلاس‌ها: {len(classes)} · گروه متصل: {connected}"
    )
    await _edit_panel(
        callback,
        body,
        [
            [InlineKeyboardButton(text="🏷 مشاهده کلاس‌ها", callback_data=f"admin:classes:school:{school_id}")],
            [InlineKeyboardButton(text="✏️ تغییر نام", callback_data=f"admin:school:rename:{school_id}")],
            [InlineKeyboardButton(text="⬅️ فهرست مدارس", callback_data="admin:schools")],
        ],
    )


async def show_classes(message: Message, session: AsyncSession, actor: int, school_id: int | None = None) -> None:
    if not await _allowed(session, actor, "classes:read"):
        await message.answer("دسترسی مشاهده کلاس‌ها را ندارید.")
        return
    schools = await list_schools(session, include_inactive=False)
    if school_id is None and len(schools) > 1:
        rows = [
            [InlineKeyboardButton(text=s.name[:36], callback_data=f"admin:classes:school:{s.id}")]
            for s in schools
        ]
        await _panel(message, "🏷 مدرسه موردنظر را انتخاب کنید:", rows)
        return
    if school_id is None:
        if not schools:
            await message.answer("مدرسه فعالی ثبت نشده است.")
            return
        school_id = schools[0].id
    classes = await list_classes(session, school_id=school_id)
    school = await session.get(School, school_id)
    rows = []
    lines = [f"🏷 <b>کلاس‌های {escape(school.name if school else '')}</b>", ""]
    for cls, _ in classes[:40]:
        group = "🔗 متصل" if cls.group_chat_id is not None else "⚠️ بدون گروه"
        status = "فعال" if cls.is_active else "در انتظار اتصال گروه"
        lines.append(f"• {escape(cls.name)} · {escape(cls.grade)} · {status} · {group} · #{cls.id}")
        rows.append([InlineKeyboardButton(text=f"{cls.name[:30]} · #{cls.id}", callback_data=f"admin:class:open:{cls.id}")])
    rows.append([InlineKeyboardButton(text="➕ ایجاد کلاس", callback_data=f"admin:class:create:{school_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ بازگشت به مدرسه", callback_data=f"admin:school:open:{school_id}")])
    await _panel(message, "\n".join(lines), rows)


@router.message(F.text == "🏷 کلاس‌ها")
async def classes_message(message: Message, session: AsyncSession) -> None:
    await show_classes(message, session, message.from_user.id)


@router.callback_query(F.data.startswith("admin:classes:school:"))
async def classes_by_school_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "classes:read"):
        await _reject_callback(callback)
        return
    try:
        school_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
    except ValueError:
        await callback.answer("شناسه مدرسه معتبر نیست.", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await show_classes(callback.message, session, callback.from_user.id, school_id)


@router.callback_query(F.data.startswith("admin:class:create:"))
async def class_create_start(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "classes:write"):
        await _reject_callback(callback)
        return
    try:
        school_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
    except ValueError:
        await callback.answer("مدرسه معتبر نیست.", show_alert=True)
        return
    school = await session.get(School, school_id)
    if school is None or not school.is_active:
        await callback.answer("مدرسه فعال پیدا نشد.", show_alert=True)
        return
    await state.clear()
    await state.update_data(class_school_id=school_id)
    await state.set_state(AdminWizard.class_name)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "➕ <b>ساخت کلاس</b>\n\nنام کلاس را وارد کنید:",
            reply_markup=panel_keyboard([[InlineKeyboardButton(text="لغو", callback_data=f"admin:classes:school:{school_id}")]]),
        )


@router.message(AdminWizard.class_name, F.text)
async def class_name_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not await _allowed(session, message.from_user.id, "classes:write"):
        await state.clear()
        await _reject_message(message)
        return
    name = (message.text or "").strip()
    if not 2 <= len(name) <= 80:
        await message.answer("نام کلاس باید بین ۲ تا ۸۰ نویسه باشد.")
        return
    await state.update_data(class_name=name)
    await state.set_state(AdminWizard.class_grade)
    await message.answer("پایه کلاس را وارد کنید؛ مثال: دهم، یازدهم، دوازدهم.")


@router.message(AdminWizard.class_grade, F.text)
async def class_grade_input(message: Message, state: FSMContext) -> None:
    grade = (message.text or "").strip()
    if not 1 <= len(grade) <= 32:
        await message.answer("نام پایه معتبر نیست.")
        return
    await state.update_data(class_grade=grade)
    await state.set_state(AdminWizard.class_section)
    await message.answer("نام شعبه/شماره کلاس را وارد کنید؛ برای ردکردن «-» بفرستید.")


@router.message(AdminWizard.class_section, F.text)
async def class_section_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not await _allowed(session, message.from_user.id, "classes:write"):
        await state.clear()
        await _reject_message(message)
        return
    data = await state.get_data()
    section = (message.text or "").strip()
    if section == "-":
        section = ""
    try:
        actor = await _ensure_actor(session, message.from_user)
        cls, code = await create_class(
            session,
            actor=message.from_user.id,
            school_id=int(data["class_school_id"]),
            name=data["class_name"],
            grade=data["class_grade"],
            section=section or None,
        )
    except (ValueError, KeyError) as exc:
        await message.answer(str(exc) or "ساخت کلاس انجام نشد.")
        return
    await state.clear()
    await message.answer(
        "✅ کلاس ساخته شد؛ تا اتصال گروه فعال نمی‌شود.\n\n"
        f"کلاس: <b>{escape(cls.name)}</b>\n"
        f"شناسه: <code>{cls.id}</code>\n\n"
        "۱) ربات اصلی ClassMate 2 را در گروه موردنظر مدیر کنید.\n"
        "۲) مدیر گروه این کد را در همان گروه برای ربات اصلی بفرستد:\n"
        f"<code>/connect {code}</code>\n\n"
        "کد اتصال ۳۰ دقیقه اعتبار دارد و فقط یک‌بار مصرف است.",
        reply_markup=MAIN_KEYBOARD,
    )


@router.callback_query(F.data.startswith("admin:class:open:"))
async def class_open(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "classes:read"):
        await _reject_callback(callback)
        return
    try:
        class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
    except ValueError:
        await callback.answer("شناسه کلاس معتبر نیست.", show_alert=True)
        return
    cls = await session.get(ClassGroup, class_id)
    if cls is None:
        await callback.answer("کلاس پیدا نشد.", show_alert=True)
        return
    school = await session.get(School, cls.school_id)
    member_count = await session.scalar(
        select(func.count(ClassMembership.id)).where(
            ClassMembership.class_id == class_id,
            ClassMembership.is_active.is_(True),
        )
    ) or 0
    subject_count = await session.scalar(
        select(func.count(ClassSubject.id)).where(ClassSubject.class_id == class_id)
    ) or 0
    body = (
        f"🏷 <b>{escape(cls.name)}</b>\n"
        f"پایه: {escape(cls.grade)} · شعبه: {escape(cls.section or '—')}\n"
        f"مدرسه: {escape(school.name if school else '—')}\n"
        f"وضعیت: {'فعال' if cls.is_active else 'غیرفعال / منتظر اتصال'}\n"
        f"گروه: {escape(cls.group_chat_title or 'متصل نیست')}\n"
        f"Group ID: {cls.group_chat_id or '—'}\n"
        f"اعضای فعال: {member_count} · درس‌های متصل: {subject_count}"
    )
    rows = [
        [InlineKeyboardButton(text="✏️ ویرایش نام کلاس", callback_data=f"admin:class:rename:{class_id}")],
        [InlineKeyboardButton(text="🎓 تغییر پایه", callback_data=f"admin:class:grade:{class_id}")],
        [InlineKeyboardButton(text="👥 اعضای کلاس", callback_data=f"admin:members:class:{class_id}")],
        [InlineKeyboardButton(text="📚 دروس کلاس", callback_data=f"admin:subjects:class:{class_id}")],
        [InlineKeyboardButton(text="⏸ غیرفعال‌سازی کلاس" if cls.is_active else "✅ فعال‌سازی کلاس",
                              callback_data=f"admin:class:toggle:{class_id}")],
        [InlineKeyboardButton(text="🔗 صدور کد اتصال گروه", callback_data=f"admin:class:groupcode:{class_id}")],
        [InlineKeyboardButton(text="✂️ قطع اتصال گروه", callback_data=f"admin:class:disconnect:{class_id}")],
        [InlineKeyboardButton(text="⬅️ فهرست کلاس‌ها", callback_data=f"admin:classes:school:{cls.school_id}")],
    ]
    await callback.answer()
    await _edit_panel(callback, body, rows)


@router.callback_query(F.data.startswith("admin:class:rename:"))
async def class_rename_start(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "classes:write"):
        await _reject_callback(callback)
        return
    try:
        class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
    except ValueError:
        await callback.answer("شناسه کلاس معتبر نیست.", show_alert=True)
        return
    cls = await session.get(ClassGroup, class_id)
    if cls is None:
        await callback.answer("کلاس پیدا نشد.", show_alert=True)
        return
    await state.clear()
    await state.update_data(edit_class_id=class_id)
    await state.set_state(AdminWizard.class_edit_name)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(f"نام جدید کلاس «{escape(cls.name)}» را وارد کنید:")


@router.message(AdminWizard.class_edit_name, F.text)
async def class_rename_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not await _allowed(session, message.from_user.id, "classes:write"):
        await state.clear()
        await _reject_message(message)
        return
    data = await state.get_data()
    name = " ".join((message.text or "").split())
    if not 2 <= len(name) <= 80:
        await message.answer("نام کلاس باید بین ۲ تا ۸۰ نویسه باشد.")
        return
    class_id = int(data["edit_class_id"])
    cls = await session.get(ClassGroup, class_id, with_for_update=True)
    if cls is None:
        await state.clear()
        await message.answer("کلاس پیدا نشد.")
        return
    before = {"name": cls.name}
    cls.name = name
    await audit_action(
        session, actor_telegram_id=message.from_user.id, action="class.rename",
        target_type="class", target_id=class_id, before=before, after={"name": name},
    )
    await state.clear()
    await message.answer(f"✅ نام کلاس به «{escape(name)}» تغییر کرد.", reply_markup=MAIN_KEYBOARD)


@router.callback_query(F.data.startswith("admin:class:grade:"))
async def class_grade_start(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "classes:write"):
        await _reject_callback(callback)
        return
    try:
        class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
    except ValueError:
        await callback.answer("شناسه کلاس معتبر نیست.", show_alert=True)
        return
    cls = await session.get(ClassGroup, class_id)
    if cls is None:
        await callback.answer("کلاس پیدا نشد.", show_alert=True)
        return
    await state.clear()
    await state.update_data(edit_class_id=class_id)
    await state.set_state(AdminWizard.class_edit_grade)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(f"پایه جدید کلاس «{escape(cls.name)}» را وارد کنید:")


@router.message(AdminWizard.class_edit_grade, F.text)
async def class_grade_edit_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not await _allowed(session, message.from_user.id, "classes:write"):
        await state.clear()
        await _reject_message(message)
        return
    data = await state.get_data()
    grade = " ".join((message.text or "").split())
    if not 1 <= len(grade) <= 32:
        await message.answer("پایه باید بین ۱ تا ۳۲ نویسه باشد.")
        return
    class_id = int(data["edit_class_id"])
    cls = await session.get(ClassGroup, class_id, with_for_update=True)
    if cls is None:
        await state.clear()
        await message.answer("کلاس پیدا نشد.")
        return
    before = {"grade": cls.grade}
    cls.grade = grade
    await audit_action(
        session, actor_telegram_id=message.from_user.id, action="class.change_grade",
        target_type="class", target_id=class_id, before=before, after={"grade": grade},
    )
    await state.clear()
    await message.answer(f"✅ پایه کلاس به «{escape(grade)}» تغییر کرد.", reply_markup=MAIN_KEYBOARD)


@router.callback_query(F.data.startswith("admin:class:toggle:"))
async def class_toggle_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "classes:write"):
        await _reject_callback(callback)
        return
    try:
        class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
        cls = await session.get(ClassGroup, class_id)
        if cls is None:
            raise ValueError("کلاس پیدا نشد.")
        updated = await set_class_active(
            session, actor=callback.from_user.id, class_id=class_id, active=not cls.is_active
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("وضعیت کلاس تغییر کرد.")
    await _edit_panel(
        callback,
        f"وضعیت کلاس «{escape(updated.name)}» به {'فعال' if updated.is_active else 'غیرفعال'} تغییر کرد.",
        [[InlineKeyboardButton(text="⬅️ بازگشت به کلاس", callback_data=f"admin:class:open:{class_id}")]],
    )



@router.callback_query(F.data.startswith("admin:class:groupcode:"))
async def class_group_code(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "groups:write"):
        await _reject_callback(callback)
        return
    try:
        class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
        cls, code = await issue_group_link_code(session, actor=callback.from_user.id, class_id=class_id)
    except (ValueError, TypeError) as exc:
        await callback.answer(str(exc) or "صدور کد انجام نشد.", show_alert=True)
        return
    await callback.answer("کد جدید صادر شد.")
    if callback.message:
        await callback.message.edit_text(
            f"🔗 <b>کد اتصال گروه کلاس {escape(cls.name)}</b>\n\n"
            f"<code>/connect {code}</code>\n\n"
            "کد ۳۰ دقیقه اعتبار دارد. مدیر گروه باید آن را در همان گروه برای ربات اصلی ارسال کند.",
            reply_markup=panel_keyboard([[InlineKeyboardButton(text="⬅️ بازگشت به کلاس", callback_data=f"admin:class:open:{class_id}")]]),
        )


@router.callback_query(F.data.startswith("admin:class:disconnect:"))
async def class_disconnect_prompt(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "groups:write"):
        await _reject_callback(callback)
        return
    try:
        class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
        cls = await session.get(ClassGroup, class_id)
    except ValueError:
        cls = None
    if cls is None:
        await callback.answer("کلاس پیدا نشد.", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "قطع اتصال گروه\n\n"
            f"کلاس: {escape(cls.name)}\n"
            "کلاس پس از قطع اتصال غیرفعال می‌شود و تا اتصال دوباره در دسترس دانش‌آموزان نخواهد بود.",
            reply_markup=panel_keyboard([
                [InlineKeyboardButton(text="✅ تأیید قطع اتصال", callback_data=f"admin:class:disconnect:confirm:{class_id}")],
                [InlineKeyboardButton(text="لغو", callback_data=f"admin:class:open:{class_id}")],
            ]),
        )


@router.callback_query(F.data.startswith("admin:class:disconnect:confirm:"))
async def class_disconnect_confirm(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "groups:write"):
        await _reject_callback(callback)
        return
    try:
        class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
        cls = await disconnect_group(session, actor=callback.from_user.id, class_id=class_id)
    except (ValueError, TypeError) as exc:
        await callback.answer(str(exc) or "قطع اتصال انجام نشد.", show_alert=True)
        return
    await callback.answer("گروه قطع شد و کلاس غیرفعال شد.")
    if callback.message:
        await callback.message.edit_text(
            f"اتصال گروه «{escape(cls.name)}» قطع شد.\nبرای فعال‌سازی دوباره کد اتصال جدید صادر کنید.",
            reply_markup=panel_keyboard([[InlineKeyboardButton(text="🔗 صدور کد اتصال", callback_data=f"admin:class:groupcode:{class_id}")]]),
        )


@router.message(F.text == "🔗 گروه‌های کلاس")
async def groups_message(message: Message, session: AsyncSession) -> None:
    if not await _allowed(session, message.from_user.id, "groups:read"):
        await _reject_message(message)
        return
    classes = await list_classes(session)
    connected = sum(1 for cls, _ in classes if cls.group_chat_id is not None)
    lines = [
        "🔗 <b>مرکز اتصال گروه‌ها</b>",
        f"گروه‌های متصل: {connected} از {len(classes)} کلاس",
        "",
    ]
    rows = []
    for cls, school in classes[:40]:
        label = f"✅ {cls.name}" if cls.group_chat_id else f"⚠️ {cls.name}"
        lines.append(f"• {escape(school.name)} / {escape(cls.name)}")
        rows.append([InlineKeyboardButton(text=label[:50], callback_data=f"admin:class:open:{cls.id}")])
    await _panel(message, "\n".join(lines), rows)


@router.message(F.text == "📚 دروس")
async def subjects_message(message: Message, session: AsyncSession) -> None:
    if not await _allowed(session, message.from_user.id, "subjects:read"):
        await _reject_message(message)
        return
    schools = await list_schools(session, include_inactive=False)
    rows = [
        [InlineKeyboardButton(text=s.name[:36], callback_data=f"admin:subjects:school:{s.id}")]
        for s in schools[:30]
    ]
    await _panel(message, "📚 <b>مدیریت دروس</b>\nابتدا مدرسه را انتخاب کنید:", rows)


@router.callback_query(F.data.startswith("admin:subjects:school:"))
async def subjects_school(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "subjects:read"):
        await _reject_callback(callback)
        return
    try:
        school_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
    except ValueError:
        await callback.answer("مدرسه معتبر نیست.", show_alert=True)
        return
    school = await session.get(School, school_id)
    if school is None:
        await callback.answer("مدرسه پیدا نشد.", show_alert=True)
        return
    subjects = await list_school_subjects(session, school_id)
    lines = [f"📚 <b>دروس {escape(school.name)}</b>", ""]
    rows = []
    for subject in subjects[:35]:
        links = await session.scalar(
            select(func.count(ClassSubject.id)).where(ClassSubject.subject_id == subject.id)
        ) or 0
        lines.append(f"• {escape(subject.name)} · {links} کلاس · #{subject.id}")
        rows.append([InlineKeyboardButton(text=f"{subject.name[:34]}", callback_data=f"admin:subject:open:{subject.id}")])
    if not subjects:
        lines.append("درسی ثبت نشده است.")
    rows.append([InlineKeyboardButton(text="➕ افزودن درس", callback_data=f"admin:subject:create:{school_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ بازگشت به مدرسه", callback_data=f"admin:school:open:{school_id}")])
    await callback.answer()
    await _edit_panel(callback, "\n".join(lines), rows)


@router.callback_query(F.data.startswith("admin:subject:create:"))
async def subject_create_start(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "subjects:write"):
        await _reject_callback(callback)
        return
    try:
        school_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
    except ValueError:
        await callback.answer("مدرسه معتبر نیست.", show_alert=True)
        return
    if await session.get(School, school_id) is None:
        await callback.answer("مدرسه پیدا نشد.", show_alert=True)
        return
    await state.clear()
    await state.update_data(subject_school_id=school_id)
    await state.set_state(AdminWizard.subject_name)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "نام درس را وارد کنید:",
            reply_markup=panel_keyboard([[InlineKeyboardButton(text="لغو", callback_data=f"admin:subjects:school:{school_id}")]]),
        )


@router.message(AdminWizard.subject_name, F.text)
async def subject_create_name(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not await _allowed(session, message.from_user.id, "subjects:write"):
        await state.clear()
        await _reject_message(message)
        return
    data = await state.get_data()
    try:
        subject = await create_school_subject(
            session, actor=message.from_user.id,
            school_id=int(data["subject_school_id"]),
            name=message.text or "",
        )
    except (ValueError, KeyError) as exc:
        await message.answer(str(exc) or "افزودن درس انجام نشد.")
        return
    await state.clear()
    await message.answer(f"✅ درس «{escape(subject.name)}» ثبت شد.", reply_markup=MAIN_KEYBOARD)


@router.callback_query(F.data.startswith("admin:subjects:class:"))
async def class_subjects_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "subjects:read"):
        await _reject_callback(callback)
        return
    try:
        class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
    except ValueError:
        await callback.answer("کلاس معتبر نیست.", show_alert=True)
        return
    cls = await session.get(ClassGroup, class_id)
    if cls is None:
        await callback.answer("کلاس پیدا نشد.", show_alert=True)
        return
    linked = list((await session.execute(
        select(ClassSubject, Subject)
        .join(Subject, ClassSubject.subject_id == Subject.id)
        .where(ClassSubject.class_id == class_id)
        .order_by(Subject.name)
    )).all())
    rows = [
        [InlineKeyboardButton(text=f"حذف · {subject.name[:28]}", callback_data=f"admin:class:subject:remove:{class_id}:{subject.id}")]
        for link, subject in linked[:25]
    ]
    rows.append([InlineKeyboardButton(text="➕ اتصال درس موجود", callback_data=f"admin:class:subjects:add:{class_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ بازگشت به کلاس", callback_data=f"admin:class:open:{class_id}")])
    lines = [f"📚 <b>دروس کلاس {escape(cls.name)}</b>", ""]
    lines.extend(f"• {escape(subject.name)}" for _, subject in linked)
    if not linked:
        lines.append("هنوز درسی به کلاس متصل نشده است.")
    await callback.answer()
    await _edit_panel(callback, "\n".join(lines), rows)


@router.callback_query(F.data.startswith("admin:class:subjects:add:"))
async def class_subject_add_list(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "subjects:write"):
        await _reject_callback(callback)
        return
    try:
        class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
    except ValueError:
        await callback.answer("کلاس معتبر نیست.", show_alert=True)
        return
    cls = await session.get(ClassGroup, class_id)
    if cls is None:
        await callback.answer("کلاس پیدا نشد.", show_alert=True)
        return
    assigned_ids = set((await session.scalars(
        select(ClassSubject.subject_id).where(ClassSubject.class_id == class_id)
    )).all())
    subjects = await list_school_subjects(session, cls.school_id)
    rows = [
        [InlineKeyboardButton(text=subject.name[:36], callback_data=f"admin:class:subject:assign:{class_id}:{subject.id}")]
        for subject in subjects if subject.id not in assigned_ids
    ]
    rows.append([InlineKeyboardButton(text="⬅️ بازگشت", callback_data=f"admin:subjects:class:{class_id}")])
    await callback.answer()
    await _edit_panel(callback, f"➕ <b>انتخاب درس برای {escape(cls.name)}</b>", rows)


@router.callback_query(F.data.startswith("admin:class:subject:assign:"))
async def class_subject_assign_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "subjects:write"):
        await _reject_callback(callback)
        return
    parts = (callback.data or "").split(":")
    try:
        class_id, subject_id = int(parts[4]), int(parts[5])
        await assign_subject(session, actor=callback.from_user.id, class_id=class_id, subject_id=subject_id)
    except (ValueError, IndexError) as exc:
        await callback.answer(str(exc) or "اتصال درس انجام نشد.", show_alert=True)
        return
    await callback.answer("درس به کلاس متصل شد.")
    if callback.message:
        callback.data = f"admin:subjects:class:{class_id}"
        await class_subjects_callback(callback, session)


@router.callback_query(F.data.startswith("admin:class:subject:remove:"))
async def class_subject_remove_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "subjects:write"):
        await _reject_callback(callback)
        return
    parts = (callback.data or "").split(":")
    try:
        class_id, subject_id = int(parts[4]), int(parts[5])
        await unassign_subject(session, actor=callback.from_user.id, class_id=class_id, subject_id=subject_id)
    except (ValueError, IndexError) as exc:
        await callback.answer(str(exc) or "برداشتن درس انجام نشد.", show_alert=True)
        return
    await callback.answer("درس از کلاس برداشته شد.")
    if callback.message:
        callback.data = f"admin:subjects:class:{class_id}"
        await class_subjects_callback(callback, session)


@router.callback_query(F.data.startswith("admin:subject:open:"))
async def subject_open_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "subjects:read"):
        await _reject_callback(callback)
        return
    try:
        subject_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
    except ValueError:
        await callback.answer("شناسه درس معتبر نیست.", show_alert=True)
        return
    subject = await session.get(Subject, subject_id)
    if subject is None:
        await callback.answer("درس پیدا نشد.", show_alert=True)
        return
    links = list((await session.execute(
        select(ClassGroup.id, ClassGroup.name, School.name)
        .join(ClassSubject, ClassSubject.class_id == ClassGroup.id)
        .join(School, ClassGroup.school_id == School.id)
        .where(ClassSubject.subject_id == subject_id)
        .order_by(School.name, ClassGroup.name)
    )).all())
    lines = [f"📚 <b>{escape(subject.name)}</b>", f"مدرسه: {subject.school_id}", "", "کلاس‌های متصل:"]
    lines.extend(f"• {escape(row[2])} / {escape(row[1])} · #{row[0]}" for row in links)
    if not links:
        lines.append("این درس هنوز به کلاسی متصل نشده است.")
    await callback.answer()
    await _edit_panel(callback, "\n".join(lines), [[InlineKeyboardButton(text="⬅️ بازگشت به دروس", callback_data=f"admin:subjects:school:{subject.school_id}")]])




@router.callback_query(F.data.startswith("admin:members:class:"))
async def members_class_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "members:read"):
        await _reject_callback(callback)
        return
    try:
        class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
    except ValueError:
        await callback.answer("کلاس معتبر نیست.", show_alert=True)
        return
    cls = await session.get(ClassGroup, class_id)
    if cls is None:
        await callback.answer("کلاس پیدا نشد.", show_alert=True)
        return
    members = await list_class_members(session, class_id)
    lines = [f"👥 <b>اعضای {escape(cls.name)}</b>", ""]
    rows = []
    for membership, user in members[:40]:
        role = membership.role.value if hasattr(membership.role, "value") else str(membership.role)
        status = "فعال" if membership.is_active else "غیرفعال"
        lines.append(f"• {escape(user.full_name)} · {role} · {status} · @{escape(user.username or '—')}")
        rows.append(
            [InlineKeyboardButton(
                text=f"{'⏸' if membership.is_active else '▶️'} {user.full_name[:28]}",
                callback_data=f"admin:member:toggle:{class_id}:{user.id}",
            )]
        )
    if not members:
        lines.append("عضوی برای این کلاس ثبت نشده است.")
    rows.append([InlineKeyboardButton(text="🔑 صدور کد عضویت", callback_data=f"admin:member:joincode:{class_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ بازگشت به کلاس", callback_data=f"admin:class:open:{class_id}")])
    await callback.answer()
    await _edit_panel(callback, "\n".join(lines), rows)


@router.callback_query(F.data.startswith("admin:member:toggle:"))
async def member_toggle_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "members:write"):
        await _reject_callback(callback)
        return
    parts = (callback.data or "").split(":")
    try:
        class_id, user_id = int(parts[3]), int(parts[4])
        membership = await session.scalar(
            select(ClassMembership).where(
                ClassMembership.class_id == class_id,
                ClassMembership.user_id == user_id,
            )
        )
        if membership is None:
            raise ValueError("عضویت پیدا نشد.")
        updated = await set_class_member_active(
            session,
            actor=callback.from_user.id,
            class_id=class_id,
            user_id=user_id,
            active=not membership.is_active,
        )
    except (ValueError, IndexError) as exc:
        await callback.answer(str(exc) or "عملیات انجام نشد.", show_alert=True)
        return
    await callback.answer("وضعیت عضویت تغییر کرد.")
    if callback.message:
        await callback.message.edit_text(
            "✅ وضعیت عضویت به‌روزرسانی شد.",
            reply_markup=panel_keyboard([[InlineKeyboardButton(text="⬅️ فهرست اعضا", callback_data=f"admin:members:class:{class_id}")]]),
        )


@router.callback_query(F.data.startswith("admin:member:joincode:"))
async def member_join_code(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "members:write"):
        await _reject_callback(callback)
        return
    try:
        class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
        cls = await session.get(ClassGroup, class_id)
        if cls is None:
            raise ValueError("کلاس پیدا نشد.")
        if not cls.is_active or cls.group_chat_id is None:
            raise ValueError("کلاس تا اتصال گروه فعال نمی‌شود؛ ابتدا گروه را متصل کنید.")
        raw = make_code(8)
        from classmate.services.permissions import hash_code, code_expiry
        cls.member_join_code_hash = hash_code(raw)
        cls.member_join_code_expires_at = code_expiry(24 * 60)
        await audit_action(
            session, actor_telegram_id=callback.from_user.id,
            action="class.member_join_code.issue", target_type="class", target_id=class_id,
            after={"expires_in_hours": 24},
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("کد عضویت صادر شد.")
    if callback.message:
        await callback.message.edit_text(
            f"🔑 کد عضویت کلاس {escape(cls.name)}\n\n"
            f"<code>{raw}</code>\n\n"
            "کاربر باید در گفت‌وگوی خصوصی ربات اصلی فرمان /join را همراه این کد بفرستد. کد ۲۴ ساعت اعتبار دارد.",
            reply_markup=panel_keyboard([[InlineKeyboardButton(text="⬅️ بازگشت به اعضا", callback_data=f"admin:members:class:{class_id}")]]),
        )




@router.callback_query(F.data == "admin:users:search")
async def user_search_start(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "members:read"):
        await _reject_callback(callback)
        return
    await state.clear()
    await state.set_state(AdminWizard.dashboard_setting)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text("نام، نام کاربری یا Telegram ID کاربر را وارد کنید:")


@router.message(AdminWizard.dashboard_setting, F.text)
async def user_search_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not await _allowed(session, message.from_user.id, "members:read"):
        await state.clear()
        await _reject_message(message)
        return
    query = (message.text or "").strip()
    stmt = select(User).order_by(User.created_at.desc())
    if query.isdecimal():
        stmt = stmt.where(User.telegram_id == int(query))
    else:
        stmt = stmt.where(
            func.lower(User.full_name).contains(query.casefold())
            | func.lower(User.username).contains(query.lstrip("@").casefold())
        )
    users = list((await session.scalars(stmt.limit(30))).all())
    await state.clear()
    lines = ["👥 <b>نتیجه جستجوی کاربران</b>", ""]
    rows = []
    for user in users:
        status = "فعال" if user.is_active else "غیرفعال"
        lines.append(f"• {escape(user.full_name)} · {user.telegram_id} · {status}")
        rows.append([InlineKeyboardButton(text=f"{user.full_name[:28]} · {status}", callback_data=f"admin:user:toggle:{user.id}")])
    if not users:
        lines.append("کاربری پیدا نشد.")
    await _panel(message, "\n".join(lines), rows)


@router.callback_query(F.data.startswith("admin:user:toggle:"))
async def user_toggle_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "members:write"):
        await _reject_callback(callback)
        return
    try:
        user_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
    except ValueError:
        await callback.answer("شناسه کاربر معتبر نیست.", show_alert=True)
        return
    user = await session.get(User, user_id, with_for_update=True)
    if user is None:
        await callback.answer("کاربر پیدا نشد.", show_alert=True)
        return
    if user.telegram_id == callback.from_user.id:
        await callback.answer("نمی‌توانید حساب خودتان را از این بخش غیرفعال کنید.", show_alert=True)
        return
    before = {"is_active": user.is_active}
    user.is_active = not user.is_active
    await audit_action(
        session, actor_telegram_id=callback.from_user.id,
        action="user.activate" if user.is_active else "user.suspend",
        target_type="user", target_id=user.id,
        before=before, after={"is_active": user.is_active},
    )
    await callback.answer("وضعیت کاربر تغییر کرد.")
    if callback.message:
        await callback.message.edit_text(
            f"✅ وضعیت {escape(user.full_name)} اکنون {'فعال' if user.is_active else 'غیرفعال'} است.",
            reply_markup=panel_keyboard([[InlineKeyboardButton(text="🔎 جستجوی دوباره", callback_data="admin:users:search")]]),
        )



@router.message(F.text == "👥 کاربران و اعضا")
async def users_message(message: Message, session: AsyncSession) -> None:
    if not await _allowed(session, message.from_user.id, "members:read"):
        await _reject_message(message)
        return
    total = await session.scalar(select(func.count(User.id))) or 0
    active = await session.scalar(select(func.count(User.id)).where(User.is_active.is_(True))) or 0
    lines = [f"👥 <b>کاربران سامانه</b>", f"کل: {total} · فعال: {active}", "", "برای مدیریت عضویت، از مسیر مدارس ← کلاس ← اعضا وارد شوید."]
    await _panel(
        message,
        "\n".join(lines),
        [
            [InlineKeyboardButton(text="🔎 جستجوی کاربر", callback_data="admin:users:search")],
            [InlineKeyboardButton(text="🏫 مدارس و کلاس‌ها", callback_data="admin:schools")],
        ],
    )


@router.message(F.text == "📥 صف بررسی")
async def approvals_message(message: Message, session: AsyncSession) -> None:
    if not await _allowed(session, message.from_user.id, "approvals:read"):
        await _reject_message(message)
        return
    items = await list_class_content_queue(session, 30)
    lines = ["📥 <b>صف بررسی محتوا</b>", ""]
    rows = []
    for label, item, cls in items:
        title = getattr(item, "title", None) or "گزارش روزانه"
        lines.append(f"• {label} · {escape(title)} · {escape(cls.name)} · #{item.id}")
        rows.append([InlineKeyboardButton(text=f"بررسی {title[:28]} · #{item.id}", callback_data=f"admin:approval:open:{label}:{item.id}")])
    if not items:
        lines.append("درخواستی در انتظار بررسی نیست.")
    await _panel(message, "\n".join(lines), rows)


@router.callback_query(F.data.startswith("admin:approval:open:"))
async def approval_open_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "approvals:read"):
        await _reject_callback(callback)
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 5:
        await callback.answer("درخواست معتبر نیست.", show_alert=True)
        return
    kind, raw_id = parts[3], parts[4]
    model = {"homework": Homework, "exam": Exam, "report": DailyReport}.get(kind)
    try:
        item = await session.get(model, int(raw_id)) if model else None
    except ValueError:
        item = None
    if item is None:
        await callback.answer("مورد پیدا نشد.", show_alert=True)
        return
    cls = await session.get(ClassGroup, item.class_id)
    subject_name = ""
    if getattr(item, "subject_id", None):
        subject = await session.get(Subject, item.subject_id)
        subject_name = subject.name if subject else ""
    body = (
        f"📥 <b>درخواست بررسی · {kind}</b>\n"
        f"کلاس: {escape(cls.name if cls else '—')}\n"
        f"عنوان: {escape(getattr(item, 'title', None) or 'گزارش روزانه')}\n"
        f"درس: {escape(subject_name or '—')}\n"
        f"وضعیت: {escape(str(item.status))}\n"
    )
    if isinstance(item, Homework):
        body += f"\n{escape(item.description[:1500])}"
    elif isinstance(item, Exam):
        body += f"\n{escape(item.scope[:1500])}"
    elif isinstance(item, DailyReport):
        body += f"\n{escape(item.body[:1500])}"
    rows = [
        [
            InlineKeyboardButton(text="✅ تأیید", callback_data=f"admin:approval:decide:approve:{kind}:{item.id}"),
            InlineKeyboardButton(text="❌ رد", callback_data=f"admin:approval:decide:reject:{kind}:{item.id}"),
        ],
        [InlineKeyboardButton(text="⬅️ بازگشت به صف", callback_data="admin:approval:list")],
    ]
    await callback.answer()
    await _edit_panel(callback, body, rows)


@router.callback_query(F.data == "admin:approval:list")
async def approval_list_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "approvals:read"):
        await _reject_callback(callback)
        return
    items = await list_class_content_queue(session, 30)
    lines = ["📥 <b>صف بررسی</b>", ""]
    rows = []
    for label, item, cls in items:
        title = getattr(item, "title", None) or "گزارش روزانه"
        lines.append(f"• {label} · {escape(title)} · {escape(cls.name)} · #{item.id}")
        rows.append([InlineKeyboardButton(text=f"{title[:28]} · #{item.id}", callback_data=f"admin:approval:open:{label}:{item.id}")])
    if not items:
        lines.append("صف بررسی خالی است.")
    await callback.answer()
    await _edit_panel(callback, "\n".join(lines), rows)


@router.callback_query(F.data.startswith("admin:approval:decide:"))
async def approval_decide_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "approvals:write"):
        await _reject_callback(callback)
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 6:
        await callback.answer("درخواست معتبر نیست.", show_alert=True)
        return
    action, kind = parts[3], parts[4]
    try:
        record_id = int(parts[5])
    except ValueError:
        await callback.answer("شناسه معتبر نیست.", show_alert=True)
        return
    model = {"homework": Homework, "exam": Exam, "report": DailyReport}.get(kind)
    item = await session.get(model, record_id) if model else None
    if item is None:
        await callback.answer("مورد پیدا نشد.", show_alert=True)
        return
    cls = await session.get(ClassGroup, item.class_id)
    if cls is None:
        await callback.answer("کلاس پیدا نشد.", show_alert=True)
        return
    school = await session.get(School, cls.school_id)
    if school is None:
        await callback.answer("مدرسه پیدا نشد.", show_alert=True)
        return
    try:
        actor = await _ensure_actor(session, callback.from_user)
        if action == "approve":
            ok, notice = await approve_record(
                session, kind, record_id, actor.id, school.id, cls.id, school.timezone,
                management_override=True,
            )
        else:
            ok, notice = await reject_record(
                session, kind, record_id, actor.id, school.id, cls.id,
                note="رد توسط مدیریت سامانه",
                management_override=True,
            )
    except (ValueError, PermissionError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if not ok:
        await callback.answer(notice, show_alert=True)
        return
    await audit_action(
        session, actor_telegram_id=callback.from_user.id,
        action=f"content.{action}", target_type=kind, target_id=record_id,
        after={"status": "APPROVED" if action == "approve" else "REJECTED"},
    )
    await callback.answer(notice)
    if callback.message:
        await callback.message.edit_text(
            f"✅ {escape(notice)}",
            reply_markup=panel_keyboard([[InlineKeyboardButton(text="⬅️ بازگشت به صف", callback_data="admin:approval:list")]]),
        )


@router.message(F.text == "📈 گزارش و سوابق")
async def reports_message(message: Message, session: AsyncSession) -> None:
    if not await _allowed(session, message.from_user.id, "reports:read"):
        await _reject_message(message)
        return
    events = await list_recent_audit(session, 25)
    lines = ["📈 <b>گزارش عملیات مدیریتی</b>", ""]
    for event in events:
        when = event.created_at.strftime("%m-%d %H:%M")
        lines.append(f"• {when} · {escape(event.action)} · {escape(event.target_type)} · {escape(event.target_id or '—')}")
    if not events:
        lines.append("هنوز عملیات مدیریتی ثبت نشده است.")
    await _panel(message, "\n".join(lines), [[InlineKeyboardButton(text="🔄 تازه‌سازی", callback_data="admin:reports:refresh")]])


@router.callback_query(F.data == "admin:reports:refresh")
async def reports_refresh_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "reports:read"):
        await _reject_callback(callback)
        return
    events = await list_recent_audit(session, 25)
    lines = ["📈 <b>گزارش عملیات مدیریتی</b>", ""]
    for event in events:
        when = event.created_at.strftime("%m-%d %H:%M")
        lines.append(f"• {when} · {escape(event.action)} · {escape(event.target_type)} · {escape(event.target_id or '—')}")
    if not events:
        lines.append("هنوز عملیات مدیریتی ثبت نشده است.")
    await callback.answer()
    await _edit_panel(callback, "\n".join(lines), [[InlineKeyboardButton(text="🔄 تازه‌سازی", callback_data="admin:reports:refresh")]])


@router.message(F.text == "🛡 مدیران")
async def admins_message(message: Message, session: AsyncSession) -> None:
    if not await is_bootstrap_owner(message.from_user.id):
        await _reject_message(message)
        return
    accounts = await list_admin_accounts(session)
    lines = ["🛡 <b>مدیران ربات مدیریت</b>", ""]
    rows = []
    for item in accounts[:30]:
        lines.append(f"• {escape(item.display_name)} · {item.telegram_id} · {ROLE_LABELS.get(item.role, item.role)} · {'فعال' if item.is_active else 'غیرفعال'}")
        if item.is_active:
            rows.append([InlineKeyboardButton(text=f"غیرفعال‌سازی {item.display_name[:22]}", callback_data=f"admin:admins:deactivate:{item.telegram_id}")])
    rows.append([InlineKeyboardButton(text="➕ افزودن مدیر", callback_data="admin:admins:create")])
    await _panel(message, "\n".join(lines), rows)


@router.callback_query(F.data == "admin:admins:create")
async def admin_create_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await is_bootstrap_owner(callback.from_user.id):
        await _reject_callback(callback)
        return
    await state.clear()
    await state.set_state(AdminWizard.admin_telegram_id)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text("Telegram ID مدیر جدید را وارد کنید:")


@router.message(AdminWizard.admin_telegram_id, F.text)
async def admin_telegram_id_input(message: Message, state: FSMContext) -> None:
    if not await is_bootstrap_owner(message.from_user.id):
        await state.clear()
        await _reject_message(message)
        return
    raw = (message.text or "").strip()
    if not raw.isdecimal() or int(raw) <= 0:
        await message.answer("Telegram ID باید عدد مثبت باشد.")
        return
    await state.update_data(new_admin_telegram_id=int(raw))
    await state.set_state(AdminWizard.admin_display_name)
    await message.answer("نام نمایشی مدیر را وارد کنید:")


@router.message(AdminWizard.admin_display_name, F.text)
async def admin_display_name_input(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not 2 <= len(name) <= 160:
        await message.answer("نام باید بین ۲ تا ۱۶۰ نویسه باشد.")
        return
    await state.update_data(new_admin_display_name=name)
    rows = [
        [InlineKeyboardButton(text=ROLE_LABELS[role], callback_data=f"admin:admins:role:{role}")]
        for role in ("operator", "reviewer", "analyst", "viewer")
    ]
    await state.set_state(None)
    await message.answer("نقش مدیریتی را انتخاب کنید:", reply_markup=panel_keyboard(rows))


@router.callback_query(F.data.startswith("admin:admins:role:"))
async def admin_create_role_callback(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if not await is_bootstrap_owner(callback.from_user.id):
        await _reject_callback(callback)
        return
    role = (callback.data or "").rsplit(":", maxsplit=1)[-1]
    if role not in ROLE_LABELS or role == "owner":
        await callback.answer("نقش معتبر نیست.", show_alert=True)
        return
    data = await state.get_data()
    telegram_id = data.get("new_admin_telegram_id")
    display_name = data.get("new_admin_display_name")
    if not telegram_id or not display_name:
        await callback.answer("فرم افزودن مدیر منقضی شده است.", show_alert=True)
        return
    account = await create_admin_account(
        session, actor=callback.from_user.id,
        telegram_id=int(telegram_id), display_name=display_name, role=role,
    )
    await state.clear()
    await callback.answer("مدیر ثبت شد.")
    if callback.message:
        await callback.message.edit_text(
            f"✅ مدیر «{escape(account.display_name)}» با نقش {ROLE_LABELS[role]} ثبت شد.",
            reply_markup=panel_keyboard([[InlineKeyboardButton(text="⬅️ فهرست مدیران", callback_data="admin:admins:list")]]),
        )


@router.callback_query(F.data == "admin:admins:list")
async def admins_list_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await is_bootstrap_owner(callback.from_user.id):
        await _reject_callback(callback)
        return
    accounts = await list_admin_accounts(session)
    rows = [
        [InlineKeyboardButton(text=f"{item.display_name[:24]} · {item.telegram_id}", callback_data=f"admin:admins:deactivate:{item.telegram_id}")]
        for item in accounts if item.is_active
    ]
    rows.append([InlineKeyboardButton(text="➕ افزودن مدیر", callback_data="admin:admins:create")])
    lines = ["🛡 <b>مدیران ثبت‌شده</b>", ""]
    lines.extend(f"• {escape(item.display_name)} · {item.telegram_id} · {ROLE_LABELS.get(item.role,item.role)} · {'فعال' if item.is_active else 'غیرفعال'}" for item in accounts)
    await callback.answer()
    await _edit_panel(callback, "\n".join(lines), rows)


@router.callback_query(F.data.startswith("admin:admins:deactivate:"))
async def admin_deactivate_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await is_bootstrap_owner(callback.from_user.id):
        await _reject_callback(callback)
        return
    try:
        telegram_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
        changed = await deactivate_admin_account(session, actor=callback.from_user.id, telegram_id=telegram_id)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("دسترسی مدیر غیرفعال شد." if changed else "مدیر پیدا نشد.")
    if callback.message:
        accounts = await list_admin_accounts(session)
        lines = ["🛡 <b>مدیران ثبت‌شده</b>", ""]
        lines.extend(f"• {escape(item.display_name)} · {item.telegram_id} · {ROLE_LABELS.get(item.role,item.role)} · {'فعال' if item.is_active else 'غیرفعال'}" for item in accounts)
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=panel_keyboard([[InlineKeyboardButton(text="➕ افزودن مدیر", callback_data="admin:admins:create")]]),
        )


@router.message(F.text == "⚙️ تنظیمات داشبورد")
async def settings_message(message: Message, session: AsyncSession) -> None:
    if not await _allowed(session, message.from_user.id, "dashboard:read"):
        await _reject_message(message)
        return
    preference = await dashboard_preference(session, message.from_user.id)
    lines = ["⚙️ <b>تنظیمات داشبورد</b>", "", "کارت‌های فعال:"]
    lines.extend(f"• {key}" for key in preference.visible_cards)
    rows = [[InlineKeyboardButton(text="شخصی‌سازی کارت‌ها", callback_data="admin:dashboard:settings")]]
    await _panel(message, "\n".join(lines), rows)


@router.message(F.text == "📣 اطلاعیه‌ها")
async def announcements_message(message: Message, session: AsyncSession) -> None:
    if not await _allowed(session, message.from_user.id, "communications:read"):
        await _reject_message(message)
        return
    classes = await list_classes(session, include_inactive=False)
    rows = [
        [InlineKeyboardButton(text=f"{cls.name[:24]} · {school.name[:18]}", callback_data=f"admin:announcement:class:{cls.id}")]
        for cls, school in classes[:30]
    ]
    await _panel(message, "📣 <b>انتشار اطلاعیه</b>\nابتدا کلاس مقصد را انتخاب کنید:", rows)


@router.callback_query(F.data.startswith("admin:announcement:class:"))
async def announcement_class_callback(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "communications:write"):
        await _reject_callback(callback)
        return
    try:
        class_id = int((callback.data or "").rsplit(":", maxsplit=1)[-1])
    except ValueError:
        await callback.answer("کلاس معتبر نیست.", show_alert=True)
        return
    cls = await session.get(ClassGroup, class_id)
    if cls is None or not cls.is_active:
        await callback.answer("کلاس فعال پیدا نشد.", show_alert=True)
        return
    await state.clear()
    await state.update_data(announcement_class_id=class_id)
    await state.set_state(AdminWizard.announcement_title)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(f"عنوان اطلاعیه برای «{escape(cls.name)}» را وارد کنید:")


@router.message(AdminWizard.announcement_title, F.text)
async def announcement_title_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not await _allowed(session, message.from_user.id, "communications:write"):
        await state.clear()
        await _reject_message(message)
        return
    title = (message.text or "").strip()
    if not 2 <= len(title) <= 180:
        await message.answer("عنوان باید بین ۲ تا ۱۸۰ نویسه باشد.")
        return
    await state.update_data(announcement_title=title)
    await state.set_state(AdminWizard.announcement_body)
    await message.answer("متن اطلاعیه را وارد کنید (حداکثر ۴۰۰۰ نویسه):")


@router.message(AdminWizard.announcement_body, F.text)
async def announcement_body_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not await _allowed(session, message.from_user.id, "communications:write"):
        await state.clear()
        await _reject_message(message)
        return
    body = (message.text or "").strip()
    if not 1 <= len(body) <= 4000:
        await message.answer("متن اطلاعیه باید بین ۱ تا ۴۰۰۰ نویسه باشد.")
        return
    data = await state.get_data()
    class_id = int(data["announcement_class_id"])
    cls = await session.get(ClassGroup, class_id)
    if cls is None or not cls.is_active or cls.group_chat_id is None:
        await state.clear()
        await message.answer("کلاس فعال و متصل به گروه نیست؛ اطلاعیه ارسال نشد.")
        return
    actor = await _ensure_actor(session, message.from_user)
    item = Announcement(
        class_id=class_id,
        title=data["announcement_title"],
        body=body,
        created_by=actor.id,
        is_active=True,
    )
    session.add(item)
    await session.flush()
    await enqueue_telegram_message(
        session,
        target_type="group",
        target_chat_id=cls.group_chat_id,
        class_id=class_id,
        text=f"📣 <b>{escape(item.title)}</b>\n\n{escape(item.body)}",
        idempotency_key=f"admin-announcement:{item.id}",
    )
    await audit_action(
        session, actor_telegram_id=message.from_user.id, action="announcement.publish",
        target_type="announcement", target_id=item.id,
        after={"class_id": class_id, "title": item.title},
    )
    await state.clear()
    await message.answer("✅ اطلاعیه ثبت شد و وارد صف ارسال شد.", reply_markup=MAIN_KEYBOARD)


@router.message(F.text == "📡 وضعیت سامانه")
async def system_status_message(message: Message, session: AsyncSession) -> None:
    if not await _allowed(session, message.from_user.id, "reports:read"):
        await _reject_message(message)
        return
    stats = await dashboard_stats(session)
    body = (
        "📡 <b>وضعیت سامانه</b>\n\n"
        f"پایگاه داده: پاسخ‌گو ({stats['database_ping_ms']} ms)\n"
        f"صف ارسال در انتظار: {stats['outbox_pending']}\n"
        f"ارسال‌های ناموفق: {stats['outbox_failed']}\n"
        f"بار سرور: {stats['server_load'] if stats['server_load'] is not None else 'N/A'}"
    )
    await _panel(message, body, [[InlineKeyboardButton(text="🔄 تازه‌سازی", callback_data="admin:system:refresh")]])


@router.callback_query(F.data == "admin:system:refresh")
async def system_status_refresh(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _allowed(session, callback.from_user.id, "reports:read"):
        await _reject_callback(callback)
        return
    stats = await dashboard_stats(session)
    body = (
        "📡 <b>وضعیت سامانه</b>\n\n"
        f"پایگاه داده: پاسخ‌گو ({stats['database_ping_ms']} ms)\n"
        f"صف ارسال در انتظار: {stats['outbox_pending']}\n"
        f"ارسال‌های ناموفق: {stats['outbox_failed']}\n"
        f"بار سرور: {stats['server_load'] if stats['server_load'] is not None else 'N/A'}"
    )
    await callback.answer("وضعیت تازه شد.")
    await _edit_panel(callback, body, [[InlineKeyboardButton(text="🔄 تازه‌سازی", callback_data="admin:system:refresh")]])


