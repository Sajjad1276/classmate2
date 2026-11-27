from aiogram import Router
from classmate.bot.handlers import broadcast, errors, groups, onboarding, phase1, phase2, start, ui


def setup_routers() -> Router:
    root = Router(name="root")
    for router in (
        broadcast.router,
        groups.router,
        onboarding.router,
        phase1.router,
        # Kept registered for backward-compatible callback/data handling; no
        # phase-two controls are exposed by the new student menu.
        phase2.router,
        ui.router,
        start.router,
        errors.router,
    ):
        root.include_router(router)
    return root
