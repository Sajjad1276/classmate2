"""Learner-facing reply and inline keyboards."""

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


def main_menu(*, is_owner: bool = False) -> ReplyKeyboardMarkup:
    """Keep only the four primary entry points on the persistent keyboard."""
    keyboard = [
        [
            KeyboardButton(text="📊 وضعیت امروز", style="primary"),
            KeyboardButton(text="📚 بخش آموزشی", style="primary"),
        ],
        [
            KeyboardButton(text="🏫 کلاس‌های من"),
            KeyboardButton(text="🔑 ورود با کد دعوت", style="success"),
        ],
        [KeyboardButton(text="🧰 امکانات بیشتر")],
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        is_persistent=True,
    )


def more_menu(*, is_owner: bool = False) -> InlineKeyboardMarkup:
    """Secondary learner tools and owner-only operations."""
    rows = [
        [InlineKeyboardButton(text="🔔 تنظیم اعلان‌ها", callback_data="p1:preferences:more")],
        [
            InlineKeyboardButton(text="❓ راهنما", callback_data="more:help"),
            InlineKeyboardButton(text="🆘 پشتیبانی", callback_data="more:support"),
        ],
    ]
    if is_owner:
        rows.append(
            [
                InlineKeyboardButton(text="📢 پیام همگانی", callback_data="broadcast:start", style="primary"),
                InlineKeyboardButton(text="🏫 مدیریت کلاس‌ها", callback_data="owner:classes:0", style="primary"),
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="‹ بازگشت به منوی اصلی", callback_data="ui:home", style="danger")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)

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
                InlineKeyboardButton(text="📅 تقویم کلاس", callback_data="info:calendar:menu", style="primary"),
                InlineKeyboardButton(text="📝 امتحانات", callback_data="info:exams:menu", style="primary"),
            ],
            [
                InlineKeyboardButton(text="📚 تکالیف", callback_data="info:homework:menu", style="primary"),
                InlineKeyboardButton(text="📋 گزارش‌های کلاسی", callback_data="info:reports:menu"),
            ],
            [InlineKeyboardButton(text="📣 اطلاعیه‌ها", callback_data="info:announcements:menu")],
            [InlineKeyboardButton(text="‹ بازگشت به منوی اصلی", callback_data="ui:home", style="danger")],
        ]
    )


# Compatibility name for code that still imports the old helper. Its contents
# are now strictly information-oriented and contain no learning-management UI.
def education_menu() -> InlineKeyboardMarkup:
    return information_menu()


def education_result_kb(back_callback: str | None = None) -> InlineKeyboardMarkup:
    if back_callback is None:
        return InlineKeyboardMarkup(inline_keyboard=[])
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="‹ بازگشت", callback_data=back_callback, style="danger")],
        ]
    )


def class_picker(
    classes: list[tuple[int, str, str]], *, action: str, back_callback: str | None = None,
    page: int = 0, page_size: int = 8
) -> InlineKeyboardMarkup:
    back_token = {
        "dashboard:today": "dashboard",
        "info:menu": "info_menu",
        "p1:dashboard": "p1_dashboard",
        "ui:home": "main_menu",
        "ui:more": "more_menu",
    }.get(back_callback)
    start = page * page_size
    rows = [
        [
            InlineKeyboardButton(
                text=(f"{name} · {school}" if school else name)[:60],
                callback_data=(
                    f"pick:{action}:{cid}:{back_token}"
                    if back_token
                    else f"pick:{action}:{cid}"
                ),
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
    if back_callback is not None:
        rows.append([InlineKeyboardButton(text="‹ بازگشت", callback_data=back_callback, style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
