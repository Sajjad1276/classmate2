"""All Telegram keyboards — reply + inline."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def main_menu(*, is_approver: bool = False, is_manager: bool = False) -> ReplyKeyboardMarkup:
    """Private-chat reply keyboard. Extra rows for elevated roles."""
    rows: list[list[KeyboardButton]] = [
        [KeyboardButton(text="🏫 کلاس‌های من"), KeyboardButton(text="➕ ثبت جدید")],
        [KeyboardButton(text="📚 تکالیف"), KeyboardButton(text="📝 امتحانات")],
        [KeyboardButton(text="📅 برنامه هفتگی"), KeyboardButton(text="📋 گزارش امروز")],
    ]
    if is_approver:
        rows.append([KeyboardButton(text="🕓 در انتظار تأیید")])
    rows.append([KeyboardButton(text="❓ راهنما")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True)


def new_record_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📚 تکلیف", callback_data="ui:new:homework"),
                InlineKeyboardButton(text="📝 امتحان", callback_data="ui:new:exam"),
            ],
            [
                InlineKeyboardButton(text="📋 گزارش روزانه", callback_data="ui:new:report"),
            ],
            [InlineKeyboardButton(text="« بازگشت", callback_data="ui:home")],
        ]
    )


def class_picker(
    classes: list[tuple[int, str, str]],
    *,
    action: str,
    page: int = 0,
    page_size: int = 8,
) -> InlineKeyboardMarkup:
    """Inline list of classes. callback_data: ui:class:{action}:{class_id}"""
    start = page * page_size
    chunk = classes[start : start + page_size]
    rows: list[list[InlineKeyboardButton]] = []
    for class_id, name, school in chunk:
        label = f"{name}"
        if school:
            label = f"{name} · {school}"
        if len(label) > 60:
            label = label[:57] + "…"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"ui:class:{action}:{class_id}",
                )
            ]
        )

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(text="‹ قبلی", callback_data=f"ui:classpage:{action}:{page - 1}")
        )
    if start + page_size < len(classes):
        nav.append(
            InlineKeyboardButton(text="بعدی ›", callback_data=f"ui:classpage:{action}:{page + 1}")
        )
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="« انصراف", callback_data="ui:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def approval_keyboard(entity_type: str, record_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ تأیید",
                    callback_data=f"approve:{entity_type}:{record_id}",
                ),
                InlineKeyboardButton(
                    text="❌ رد",
                    callback_data=f"reject:{entity_type}:{record_id}",
                ),
            ]
        ]
    )


def after_approve_keyboard(entity_type: str, record_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="مشاهده جزئیات",
                    callback_data=f"ui:view:{entity_type}:{record_id}",
                )
            ]
        ]
    )


def confirm_cancel_row() -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(text="لغو", callback_data="ui:cancel"),
    ]
