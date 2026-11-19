from aiogram import Router

from classmate.bot.handlers import common, start


def setup_routers() -> Router:
    """Assemble all domain routers. Order matters."""
    root = Router(name="root")

    # Specific routers first
    root.include_router(start.router)
    root.include_router(common.router)

    return root
