from aiogram import Router

from classmate.bot.handlers import approvals, errors, group, phase1, phase2, start, ui, wizards


def setup_routers() -> Router:
    root = Router(name="root")
    root.include_router(group.router)
    root.include_router(phase1.router)
    root.include_router(phase2.router)
    root.include_router(ui.router)
    root.include_router(wizards.router)
    root.include_router(start.router)
    root.include_router(approvals.router)
    root.include_router(errors.router)
    return root
