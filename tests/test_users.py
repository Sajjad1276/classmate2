import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

os.environ.setdefault("BOT_TOKEN", "0000000000:TEST_TOKEN_FOR_TESTS_ONLY")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from classmate.bot.middlewares import record_private_activity
from classmate.db.base import Base
from classmate.services.users import ensure_user


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_ensure_user_creates_new(session: AsyncSession):
    user = await ensure_user(
        session,
        telegram_id=111,
        username="ali",
        full_name="Ali Reza",
        private_chat_started=True,
    )
    assert user.id is not None
    assert user.telegram_id == 111
    assert user.full_name == "Ali Reza"
    assert user.private_chat_started is True
    assert user.first_private_started_at is not None
    assert user.last_seen_at is not None


@pytest.mark.asyncio
async def test_ensure_user_updates_existing(session: AsyncSession):
    u1 = await ensure_user(
        session, telegram_id=222, username="old", full_name="Old Name"
    )
    await session.commit()

    u2 = await ensure_user(
        session,
        telegram_id=222,
        username="new",
        full_name="New Name",
        private_chat_started=True,
    )
    assert u1.id == u2.id
    assert u2.username == "new"
    assert u2.full_name == "New Name"
    assert u2.private_chat_started is True
    assert u2.first_private_started_at is not None
    assert u2.last_seen_at is not None


@pytest.mark.asyncio
async def test_private_activity_updates_last_seen_without_saving_message_content(session: AsyncSession):
    user = await ensure_user(
        session,
        telegram_id=333,
        username="active_user",
        full_name="Active User",
        private_chat_started=True,
    )
    old_seen = datetime.now(UTC) - timedelta(days=1)
    user.last_seen_at = old_seen
    await session.flush()

    private_event = SimpleNamespace(
        message=SimpleNamespace(
            from_user=SimpleNamespace(id=333),
            chat=SimpleNamespace(type="private"),
            text="private user message",
        ),
        callback_query=None,
    )
    await record_private_activity(session, private_event)
    await session.refresh(user)

    assert user.last_seen_at is not None
    assert user.last_seen_at.replace(tzinfo=UTC) > old_seen

    previous_seen = user.last_seen_at
    group_event = SimpleNamespace(
        message=SimpleNamespace(
            from_user=SimpleNamespace(id=333),
            chat=SimpleNamespace(type="group"),
            text="group message",
        ),
        callback_query=None,
    )
    await record_private_activity(session, group_event)
    await session.refresh(user)
    assert user.last_seen_at == previous_seen
