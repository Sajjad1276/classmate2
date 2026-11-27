"""Learner-facing reply and inline keyboards."""

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


def main_menu(*, is_owner: bool = False) -> ReplyKeyboardMarkup:
    """Persistent student information menu.

    The reply keyboard is the only general route back to the product's main
    areas. No inline "main menu" button is intentionally exposed.
    """
    keyboard = [
        [
            KeyboardButton(text="📊 وضعیت امروز", style="primary"),
            KeyboardButton(text="📅 تقویم کلاس", style="primary"),
        ],
        [
            KeyboardButton(text="📝 امتحانات", style="primary"),
            KeyboardButton(text="📚 تکالیف", style="primary"),
        ],
        [
            KeyboardButton(text="📋 گزارش‌های کلاسی"),
            KeyboardButton(text="📣 اطلاعیه‌ها"),
        ],
        [
            KeyboardButton(text="🏫 کلاس‌های من"),
            KeyboardButton(text="🔔 اعلان‌ها"),
        ],
        [KeyboardButton(text="❓ راهنما"), KeyboardButton(text="🆘 پشتیبانی")],
    ]
    if is_owner:
        keyboard[3].append(KeyboardButton(text="🔴 مدیریت", style="danger"))
        keyboard.append([KeyboardButton(text="🏫 کلاس‌ها", style="primary")])
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        is_persistent=True,
    )


def cancel_kb(*, selective: bool = False) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ انصراف", style="danger")]],
        resize_keyboard=True,
        selective=selective,
    )


def information_menu() -> InlineKeyboardMarkup:
    """Inline entry points for the read-only class information areas."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📅 تقویم کلاس", callback_data="info:calendar", style="primary"),
                InlineKeyboardButton(text="📝 امتحانات", callback_data="info:exams", style="primary"),
            ],
            [
                InlineKeyboardButton(text="📚 تکالیف", callback_data="info:homework", style="primary"),
                InlineKeyboardButton(text="📋 گزارش‌های کلاسی", callback_data="info:reports"),
            ],
            [InlineKeyboardButton(text="📣 اطلاعیه‌ها", callback_data="info:announcements")],
            [InlineKeyboardButton(text="🔄 به‌روزرسانی", callback_data="dashboard:today")],
        ]
    )


# Compatibility name for code that still imports the old helper. Its contents
# are now strictly information-oriented and contain no learning-management UI.
def education_menu() -> InlineKeyboardMarkup:
    return information_menu()


def education_result_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="‹ بازگشت", callback_data="ui:back")],
        ]
    )


def class_picker(
    classes: list[tuple[int, str, str]], *, action: str, page: int = 0, page_size: int = 8
) -> InlineKeyboardMarkup:
    start = page * page_size
    rows = [
        [
            InlineKeyboardButton(
                text=(f"{name} · {school}" if school else name)[:60],
                callback_data=f"pick:{action}:{cid}",
                style="primary",
            )
        ]
        for cid, name, school in classes[start : start + page_size]
    ]
    nav = []
    if page:
        nav.append(
            InlineKeyboardButton(text="‹ صفحه قبل", callback_data=f"pickpage:{action}:{page - 1}")
        )
    if start + page_size < len(classes):
        nav.append(
            InlineKeyboardButton(text="صفحه بعد ›", callback_data=f"pickpage:{action}:{page + 1}")
        )
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="‹ بازگشت", callback_data="ui:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
