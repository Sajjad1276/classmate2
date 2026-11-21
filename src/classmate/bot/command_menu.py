"""Public command menu for the learner-facing bot."""

from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats

PRIVATE = [BotCommand(command="start", description="نمایش منوی اصلی ClassMate")]
GROUP = [BotCommand(command="start", description="نمایش راهنمای سریع")]


async def setup_default_commands(bot: Bot) -> None:
    await bot.set_my_commands(PRIVATE, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(GROUP, scope=BotCommandScopeAllGroupChats())
