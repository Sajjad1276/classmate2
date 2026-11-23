from aiogram import Router
from classmate.bot.handlers import errors, group_link, onboarding, phase1, phase2, queries, start, ui


def setup_routers() -> Router:
    root = Router(name="root")
    for router in (
        group_link.router,
        onboarding.router,
        queries.router,
        phase1.router,
        phase2.router,
        ui.router,
        start.router,
        errors.router,
    ):
        root.include_router(router)
    return root
