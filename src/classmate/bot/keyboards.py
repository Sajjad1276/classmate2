from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="کلاس‌های من"), KeyboardButton(text="ثبت اطلاعات جدید")],
            [KeyboardButton(text="تکالیف"), KeyboardButton(text="امتحانات")],
            [KeyboardButton(text="برنامه هفتگی"), KeyboardButton(text="راهنما")],
        ],
        resize_keyboard=True,
    )


def approval_keyboard(entity_type: str, record_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="تأیید", callback_data=f"approve:{entity_type}:{record_id}"
                ),
                InlineKeyboardButton(
                    text="رد", callback_data=f"reject:{entity_type}:{record_id}"
                ),
            ]
        ]
    )
