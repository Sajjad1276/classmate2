"""BotFather-style command menu per chat scope."""

from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
)

COMMON_PRIVATE = [
    BotCommand(command="start", description="شروع و منوی اصلی"),
    BotCommand(command="classes", description="فهرست کلاس‌های من"),
    BotCommand(command="homework", description="تکالیف تأییدشده"),
    BotCommand(command="exams", description="امتحانات"),
    BotCommand(command="today", description="گزارش امروز"),
    BotCommand(command="week", description="برنامه هفتگی"),
    BotCommand(command="help", description="راهنما"),
    BotCommand(command="cancel", description="لغو فرم"),
]

APPROVER_EXTRA = [
    BotCommand(command="pending", description="درخواست‌های در انتظار تأیید"),
]

MANAGER_EXTRA = [
    BotCommand(command="class_code", description="کد اتصال گروه و عضویت"),
    BotCommand(command="subject_add", description="افزودن درس"),
    BotCommand(command="timetable_set", description="تنظیم برنامه هفتگی"),
]

ADMIN_EXTRA = [
    BotCommand(command="school_new", description="ساخت مدرسه"),
    BotCommand(command="class_new", description="ساخت کلاس"),
]

GROUP_COMMANDS = [
    BotCommand(command="homework", description="تکالیف کلاس"),
    BotCommand(command="exams", description="امتحانات"),
    BotCommand(command="today", description="گزارش امروز"),
    BotCommand(command="week", description="برنامه هفتگی"),
    BotCommand(command="pending", description="در انتظار تأیید"),
    BotCommand(command="new_homework", description="ثبت تکلیف"),
    BotCommand(command="new_exam", description="ثبت امتحان"),
    BotCommand(command="new_report", description="ثبت گزارش"),
    BotCommand(command="subject_add", description="افزودن درس"),
    BotCommand(command="connect", description="اتصال گروه به کلاس"),
]


async def setup_default_commands(bot: Bot) -> None:
    await bot.set_my_commands(COMMON_PRIVATE, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(GROUP_COMMANDS, scope=BotCommandScopeAllGroupChats())


async def sync_private_command_menu(
    bot: Bot,
    chat_id: int,
    *,
    is_platform_admin: bool = False,
    is_school_admin: bool = False,
    is_class_manager: bool = False,
    is_approver: bool = False,
) -> None:
    commands = list(COMMON_PRIVATE)
    if is_approver:
        commands = commands + APPROVER_EXTRA
    if is_class_manager or is_school_admin or is_platform_admin:
        commands = commands + MANAGER_EXTRA
    if is_platform_admin or is_school_admin:
        commands = commands + ADMIN_EXTRA
    # de-dupe by command name keeping order
    seen: set[str] = set()
    unique: list[BotCommand] = []
    for c in commands:
        if c.command not in seen:
            seen.add(c.command)
            unique.append(c)
    await bot.set_my_commands(unique, scope=BotCommandScopeChat(chat_id=chat_id))
