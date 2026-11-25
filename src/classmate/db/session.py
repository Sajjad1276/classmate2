from collections.abc import AsyncGenerator
import logging

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from classmate.core.config import get_settings

logger = logging.getLogger(__name__)

def normalize_async_database_url(database_url: str) -> str:
    """Force PostgreSQL runtime connections onto the asyncpg driver.

    Railway may provide a psycopg-flavoured URL. The application uses
    SQLAlchemy's async engine everywhere, including the product bot and
    delivery worker, so it must not depend on a sync-only or absent driver.
    """
    url = make_url(database_url)
    if url.get_backend_name() == "postgresql" and url.get_driver_name() != "asyncpg":
        url = url.set(drivername="postgresql+asyncpg")
        return url.render_as_string(hide_password=False)
    return database_url


settings = get_settings()

engine = create_async_engine(
    normalize_async_database_url(settings.database_url),
    echo=False,
    pool_pre_ping=True,
)

SessionFactory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            try:
                await session.rollback()
            except Exception:
                logger.exception("Database rollback failed in session dependency")
            raise
