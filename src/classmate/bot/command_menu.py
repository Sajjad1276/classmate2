"""Minimal command menu — UX is button-first; only /start remains essential."""

from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats, BotCommandScopeChat

PRIVATE = [
    BotCommand(command="start", description="باز کردن منوی اصلی"),
]

GROUP = [
    BotCommand(command="start", description="راهنمای کوتاه"),
]


async def setup_default_commands(bot: Bot) -> None:
    await bot.set_my_commands(PRIVATE, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(GROUP, scope=BotCommandScopeAllGroupChats())


async def sync_private_command_menu(
    bot: Bot,
    chat_id: int,
    *,
    is_platform_admin: bool = False,
    is_school_admin: bool = False,
    is_class_manager: bool = False,
    is_approver: bool = False,
) -> None:
    await bot.set_my_commands(PRIVATE, scope=BotCommandScopeChat(chat_id=chat_id))
