"""Reply + inline keyboards — primary UX surface."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def main_menu(
    *,
    is_approver: bool = False,
    is_manager: bool = False,
    is_admin: bool = False,
) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = [
        [KeyboardButton(text="🏫 کلاس‌های من"), KeyboardButton(text="➕ ثبت جدید")],
        [KeyboardButton(text="📚 تکالیف"), KeyboardButton(text="📝 امتحانات")],
        [KeyboardButton(text="📅 برنامه هفتگی"), KeyboardButton(text="📋 گزارش امروز")],
    ]
    if is_approver:
        rows.append([KeyboardButton(text="🕓 در انتظار تأیید")])
    if is_manager:
        rows.append([KeyboardButton(text="⚙️ مدیریت کلاس")])
    if is_admin:
        rows.append([KeyboardButton(text="🏛 مدیریت سامانه")])
    rows.append([KeyboardButton(text="🔑 عضویت با کد"), KeyboardButton(text="❓ راهنما")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True)


def cancel_kb(*, selective: bool = False) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ انصراف")]],
        resize_keyboard=True,
        selective=selective,
    )


def skip_or_cancel_kb(*, selective: bool = False) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⏭ بدون موعد")],
            [KeyboardButton(text="❌ انصراف")],
        ],
        resize_keyboard=True,
        selective=selective,
    )


def new_record_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📚 تکلیف", callback_data="wiz:hw"),
                InlineKeyboardButton(text="📝 امتحان", callback_data="wiz:exam"),
            ],
            [InlineKeyboardButton(text="📋 گزارش روزانه", callback_data="wiz:report")],
            [InlineKeyboardButton(text="« بازگشت", callback_data="ui:home")],
        ]
    )


def manage_class_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔗 کد اتصال و عضویت", callback_data="mgr:codes")],
            [InlineKeyboardButton(text="📖 افزودن درس", callback_data="mgr:subject")],
            [InlineKeyboardButton(text="🗓 تنظیم برنامه هفتگی", callback_data="mgr:timetable")],
            [InlineKeyboardButton(text="« بازگشت", callback_data="ui:home")],
        ]
    )


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏛 ساخت مدرسه", callback_data="adm:school")],
            [InlineKeyboardButton(text="🎓 ساخت کلاس", callback_data="adm:class")],
            [InlineKeyboardButton(text="« بازگشت", callback_data="ui:home")],
        ]
    )


def group_panel(*, class_id: int, class_name: str = "") -> InlineKeyboardMarkup:
    cid = class_id
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📚 تکالیف", callback_data=f"g:view:homework:{cid}"),
                InlineKeyboardButton(text="📝 امتحانات", callback_data=f"g:view:exams:{cid}"),
            ],
            [
                InlineKeyboardButton(text="📋 امروز", callback_data=f"g:view:today:{cid}"),
                InlineKeyboardButton(text="📅 برنامه", callback_data=f"g:view:week:{cid}"),
            ],
            [
                InlineKeyboardButton(text="➕ تکلیف", callback_data=f"g:new:hw:{cid}"),
                InlineKeyboardButton(text="➕ امتحان", callback_data=f"g:new:exam:{cid}"),
                InlineKeyboardButton(text="➕ گزارش", callback_data=f"g:new:report:{cid}"),
            ],
            [
                InlineKeyboardButton(text="🕓 در انتظار", callback_data=f"g:view:pending:{cid}"),
                InlineKeyboardButton(text="🔄 بروزرسانی پنل", callback_data=f"g:panel:{cid}"),
            ],
        ]
    )


def class_picker(
    classes: list[tuple[int, str, str]],
    *,
    action: str,
    page: int = 0,
    page_size: int = 8,
) -> InlineKeyboardMarkup:
    start = page * page_size
    chunk = classes[start : start + page_size]
    rows: list[list[InlineKeyboardButton]] = []
    for class_id, name, school in chunk:
        label = f"{name} · {school}" if school else name
        if len(label) > 60:
            label = label[:57] + "…"
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"pick:{action}:{class_id}")]
        )
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="‹ قبلی", callback_data=f"pickpage:{action}:{page - 1}"))
    if start + page_size < len(classes):
        nav.append(InlineKeyboardButton(text="بعدی ›", callback_data=f"pickpage:{action}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="« انصراف", callback_data="ui:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def subject_picker(subjects: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=name, callback_data=f"subj:{sid}")]
        for sid, name in subjects
    ]
    rows.append([InlineKeyboardButton(text="❌ انصراف", callback_data="ui:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def day_picker() -> InlineKeyboardMarkup:
    days = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i, d in enumerate(days):
        row.append(InlineKeyboardButton(text=d, callback_data=f"day:{i}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="❌ انصراف", callback_data="ui:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def school_picker(schools: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=name, callback_data=f"sch:{sid}")]
        for sid, name in schools
    ]
    rows.append([InlineKeyboardButton(text="❌ انصراف", callback_data="ui:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def approval_keyboard(entity_type: str, record_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ تأیید", callback_data=f"approve:{entity_type}:{record_id}"),
                InlineKeyboardButton(text="❌ رد", callback_data=f"reject:{entity_type}:{record_id}"),
            ]
        ]
    )
