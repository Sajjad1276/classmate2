from aiogram import Router

from classmate.bot.handlers import approvals, group, start, ui, wizards


def setup_routers() -> Router:
    root = Router(name="root")
    root.include_router(group.router)
    root.include_router(ui.router)
    root.include_router(wizards.router)
    root.include_router(start.router)
    root.include_router(approvals.router)
    return root
