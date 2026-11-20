"""Database package exports.

Keep engine/session initialization lazy so schema inspection and Alembic can
import Base/models without loading application settings such as BOT_TOKEN.
"""

from typing import Any

from classmate.db.base import Base

__all__ = ["Base", "SessionFactory", "get_session"]


def __getattr__(name: str) -> Any:
    if name in {"SessionFactory", "get_session"}:
        from classmate.db.session import SessionFactory, get_session

        return {"SessionFactory": SessionFactory, "get_session": get_session}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
