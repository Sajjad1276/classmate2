from aiogram import Router
from classmate.bot.handlers import errors, groups, onboarding, phase1, phase2, start, ui


def setup_routers() -> Router:
    root = Router(name="root")
    for router in (
        groups.router,
        onboarding.router,
        phase1.router,
        phase2.router,
        ui.router,
        start.router,
        errors.router,
    ):
        root.include_router(router)
    return root
