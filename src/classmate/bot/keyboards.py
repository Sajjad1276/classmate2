"""Reply + inline keyboards — primary UX surface."""

from aiogram.types import (
    ChatAdministratorRights,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    KeyboardButtonRequestChat,
    ReplyKeyboardMarkup,
)


def main_menu(
    *,
    is_approver: bool = False,
    is_manager: bool = False,
    is_admin: bool = False,
) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = [
        [KeyboardButton(text="🏠 داشبورد من"), KeyboardButton(text="🏫 کلاس‌های من")],
        [KeyboardButton(text="🎓 بخش آموزشی"), KeyboardButton(text="➕ ثبت اطلاعات")],
        [KeyboardButton(text="🔎 جست‌وجو"), KeyboardButton(text="📣 اطلاعیه‌ها")],
        [KeyboardButton(text="🔔 اعلان‌ها"), KeyboardButton(text="🆘 پشتیبانی")],
    ]
    if is_approver:
        rows.append([KeyboardButton(text="🕓 موارد در انتظار بررسی")])
    if is_manager:
        rows.append([KeyboardButton(text="⚙️ مدیریت")])
    if is_admin:
        rows.append([KeyboardButton(text="🏛 مدیریت سامانه")])
    rows.append([KeyboardButton(text="❓ راهنما"), KeyboardButton(text="🔑 ورود با کد دعوت")])
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
            [KeyboardButton(text="⏭ بدون تعیین موعد")],
            [KeyboardButton(text="❌ انصراف")],
        ],
        resize_keyboard=True,
        selective=selective,
    )


def new_record_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📚 ثبت تکلیف", callback_data="wiz:hw", style="success"),
                InlineKeyboardButton(text="📝 ثبت امتحان", callback_data="wiz:exam", style="success"),
            ],
            [InlineKeyboardButton(text="📋 ثبت گزارش روزانه", callback_data="wiz:report", style="success")],
            [InlineKeyboardButton(text="« بازگشت به منو", callback_data="ui:home", style="danger")],
        ]
    )


def education_menu() -> InlineKeyboardMarkup:
    """Compact learning hub; detailed actions stay in inline navigation."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📚 تکالیف", callback_data="menu:homework"), InlineKeyboardButton(text="📝 امتحانات", callback_data="menu:exams")],
        [InlineKeyboardButton(text="📅 برنامه", callback_data="menu:week"), InlineKeyboardButton(text="📋 گزارش امروز", callback_data="menu:today")],
        [InlineKeyboardButton(text="📤 ارسال تکلیف", callback_data="menu:submit"), InlineKeyboardButton(text="📊 نمرات", callback_data="menu:grades")],
        [InlineKeyboardButton(text="🟢 حضور", callback_data="menu:attendance"), InlineKeyboardButton(text="🗳 نظرسنجی", callback_data="menu:polls")],
        [InlineKeyboardButton(text="« بازگشت", callback_data="ui:home", style="danger")],
    ])


def management_menu(*, is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="⚙️ کلاس", callback_data="menu:class_manage", style="primary"), InlineKeyboardButton(text="🗂 محتوا", callback_data="menu:content", style="primary")],
        [InlineKeyboardButton(text="👨‍🏫 ارزیابی", callback_data="menu:evaluation"), InlineKeyboardButton(text="📝 پاسخ‌ها", callback_data="menu:reviews")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton(text="🏛 سامانه", callback_data="menu:admin")])
    rows.append([InlineKeyboardButton(text="« بازگشت", callback_data="ui:home", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def manage_class_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔗 افزودن گروه کلاس", callback_data="mgr:connect_group", style="success")],
            [InlineKeyboardButton(text="🔑 کدهای اتصال و عضویت", callback_data="mgr:codes", style="primary")],
            [InlineKeyboardButton(text="📖 مدیریت درس‌ها", callback_data="mgr:subject", style="primary")],
            [InlineKeyboardButton(text="🗓 تنظیم برنامه هفتگی", callback_data="mgr:timetable", style="primary")],
            [InlineKeyboardButton(text="« بازگشت به منو", callback_data="ui:home", style="danger")],
        ]
    )


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏛 ایجاد مدرسه", callback_data="adm:school", style="success")],
            [InlineKeyboardButton(text="🎓 ایجاد کلاس", callback_data="adm:class", style="success")],
            [InlineKeyboardButton(text="« بازگشت به منو", callback_data="ui:home", style="danger")],
        ]
    )


def group_panel(*, class_id: int, class_name: str = "") -> InlineKeyboardMarkup:
    cid = class_id
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📚 تکالیف من", callback_data=f"g:view:homework:{cid}", style="primary"),
                InlineKeyboardButton(text="📝 تقویم امتحانات", callback_data=f"g:view:exams:{cid}", style="primary"),
            ],
            [
                InlineKeyboardButton(text="📋 گزارش‌های امروز", callback_data=f"g:view:today:{cid}", style="primary"),
                InlineKeyboardButton(text="📅 برنامه هفتگی", callback_data=f"g:view:week:{cid}", style="primary"),
            ],
            [
                InlineKeyboardButton(text="➕ تکلیف جدید", callback_data=f"g:new:hw:{cid}", style="success"),
                InlineKeyboardButton(text="➕ امتحان جدید", callback_data=f"g:new:exam:{cid}", style="success"),
                InlineKeyboardButton(text="➕ گزارش جدید", callback_data=f"g:new:report:{cid}", style="success"),
            ],
            [
                InlineKeyboardButton(text="🕓 در انتظار بررسی", callback_data=f"g:view:pending:{cid}", style="primary"),
                InlineKeyboardButton(text="🔄 به‌روزرسانی پنل", callback_data=f"g:panel:{cid}", style="primary"),
            ],
        ]
    )


def group_request_kb(request_id: int) -> ReplyKeyboardMarkup:
    """Ask Telegram to select a group where the user and bot are administrators."""
    rights = ChatAdministratorRights(
        is_anonymous=False,
        can_manage_chat=True,
        can_delete_messages=True,
        can_manage_video_chats=False,
        can_restrict_members=False,
        can_promote_members=False,
        can_change_info=True,
        can_invite_users=True,
        can_post_stories=False,
        can_edit_stories=False,
        can_delete_stories=False,
        can_send_welcome_messages=False,
        can_post_messages=True,
        can_edit_messages=True,
        can_pin_messages=True,
        can_manage_topics=True,
    )
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="✅ انتخاب گروه کلاس",
                    style="primary",
                    request_chat=KeyboardButtonRequestChat(
                        request_id=request_id,
                        chat_is_channel=False,
                        chat_is_forum=False,
                        user_administrator_rights=rights,
                        bot_administrator_rights=rights,
                        request_title=True,
                        request_username=True,
                    ),
                )
            ],
            [KeyboardButton(text="❌ انصراف", style="danger")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def connect_group_kb(class_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔗 افزودن گروه کلاس",
                    callback_data=f"mgr:connect_group:{class_id}",
                    style="success",
                )
            ]
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
        nav.append(InlineKeyboardButton(text="‹ صفحه قبل", callback_data=f"pickpage:{action}:{page - 1}"))
    if start + page_size < len(classes):
        nav.append(InlineKeyboardButton(text="صفحه بعد ›", callback_data=f"pickpage:{action}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="« لغو عملیات", callback_data="ui:home")])
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
                InlineKeyboardButton(text="✅ تأیید نهایی", callback_data=f"approve:{entity_type}:{record_id}"),
                InlineKeyboardButton(text="❌ رد درخواست", callback_data=f"reject:{entity_type}:{record_id}"),
            ]
        ]
    )
