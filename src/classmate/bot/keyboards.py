"""Learner-facing reply and inline keyboards."""

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🏠 داشبورد من", style="primary"),
                KeyboardButton(text="🏫 کلاس‌های من"),
            ],
            [
                KeyboardButton(text="🎓 بخش آموزشی", style="primary"),
                KeyboardButton(text="🔎 جست‌وجو"),
            ],
            [KeyboardButton(text="🔑 ورود با کد دعوت", style="success")],
            [KeyboardButton(text="❓ راهنما"), KeyboardButton(text="🆘 پشتیبانی")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def cancel_kb(*, selective: bool = False) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ انصراف", style="danger")]],
        resize_keyboard=True,
        selective=selective,
    )


def education_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📚 تکالیف", callback_data="menu:homework"),
                InlineKeyboardButton(text="📝 امتحانات", callback_data="menu:exams"),
            ],
            [
                InlineKeyboardButton(text="📅 برنامه", callback_data="menu:week"),
                InlineKeyboardButton(text="📋 گزارش امروز", callback_data="menu:today"),
            ],
            [
                InlineKeyboardButton(text="📤 ارسال تکلیف", callback_data="menu:submit"),
                InlineKeyboardButton(text="📊 نمرات", callback_data="menu:grades"),
            ],
            [
                InlineKeyboardButton(text="🟢 حضور", callback_data="menu:attendance"),
                InlineKeyboardButton(text="🗳 نظرسنجی", callback_data="menu:polls"),
            ],
            [InlineKeyboardButton(text="« بازگشت", callback_data="ui:home", style="danger")],
        ]
    )


def education_result_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎓 بازگشت به بخش آموزشی", callback_data="menu:education", style="primary"
                )
            ],
            [InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="ui:home", style="danger")],
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
    rows.append([InlineKeyboardButton(text="« لغو عملیات", callback_data="ui:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
