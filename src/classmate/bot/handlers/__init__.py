from aiogram import Router

from classmate.bot.handlers import approvals, common, onboarding, queries, records, start


def setup_routers() -> Router:
    root = Router(name="root")
    root.include_router(start.router)
    root.include_router(onboarding.router)
    root.include_router(records.router)
    root.include_router(queries.router)
    root.include_router(approvals.router)
    root.include_router(common.router)
    return root
