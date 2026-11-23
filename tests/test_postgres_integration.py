"""PostgreSQL-only integration coverage for row locking and outbox idempotency."""

import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from classmate.core.enums import OutboxStatus, Role
from classmate.db.base import Base
from classmate.db.models import ClassGroup, ClassMembership, School, TelegramOutbox, User
from classmate.services.outbox import enqueue_telegram_message


@pytest.mark.asyncio
async def test_postgres_outbox_is_durable_and_claims_respect_skip_locked():
    database_url = os.getenv("POSTGRES_TEST_URL")
    if not database_url:
        pytest.skip("POSTGRES_TEST_URL is not configured")

    engine = create_async_engine(database_url, pool_size=4, max_overflow=0)
    try:
        # This URL is a dedicated ephemeral GitHub Actions service database.
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)

        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            async with session.begin():
                school = School(name="PostgreSQL integration", timezone="UTC")
                session.add(school)
                await session.flush()
                class_group = ClassGroup(
                    school_id=school.id,
                    name="integration class",
                    grade="test",
                    section="1",
                    group_chat_id=-1009876543210,
                )
                user = User(
                    telegram_id=987654321,
                    username="integration_student",
                    full_name="Integration Student",
                    private_chat_started=True,
                )
                session.add_all([class_group, user])
                await session.flush()
                session.add(
                    ClassMembership(
                        class_id=class_group.id,
                        user_id=user.id,
                        role=Role.STUDENT,
                        is_active=True,
                    )
                )
                first = await enqueue_telegram_message(
                    session,
                    target_type="group",
                    target_chat_id=class_group.group_chat_id,
                    class_id=class_group.id,
                    text="first durable message",
                    idempotency_key="pg-integration:one",
                    scheduled_at=datetime.now(timezone.utc),
                )
                repeated = await enqueue_telegram_message(
                    session,
                    target_type="group",
                    target_chat_id=class_group.group_chat_id,
                    class_id=class_group.id,
                    text="first durable message",
                    idempotency_key="pg-integration:one",
                    scheduled_at=datetime.now(timezone.utc),
                )
                second = await enqueue_telegram_message(
                    session,
                    target_type="group",
                    target_chat_id=class_group.group_chat_id,
                    class_id=class_group.id,
                    text="second durable message",
                    idempotency_key="pg-integration:two",
                    scheduled_at=datetime.now(timezone.utc),
                )
                assert first.id == repeated.id
                assert second.id != first.id

        # Two independent PostgreSQL transactions must not claim the same row.
        async with engine.connect() as first_connection:
            async with engine.connect() as second_connection:
                first_tx = await first_connection.begin()
                second_tx = await second_connection.begin()
                try:
                    first_id = (
                        await first_connection.execute(
                            select(TelegramOutbox.id)
                            .where(TelegramOutbox.status == OutboxStatus.PENDING)
                            .order_by(TelegramOutbox.id)
                            .limit(1)
                            .with_for_update(skip_locked=True)
                        )
                    ).scalar_one()
                    second_id = (
                        await second_connection.execute(
                            select(TelegramOutbox.id)
                            .where(TelegramOutbox.status == OutboxStatus.PENDING)
                            .order_by(TelegramOutbox.id)
                            .limit(1)
                            .with_for_update(skip_locked=True)
                        )
                    ).scalar_one()
                    assert first_id != second_id
                finally:
                    await second_tx.rollback()
                    await first_tx.rollback()

        # A new session sees the committed rows after the enqueue transaction.
        async with sessions() as session:
            count = await session.scalar(
                select(func.count()).select_from(TelegramOutbox).where(
                    TelegramOutbox.id.in_([first.id, second.id])
                )
            )
            assert count == 2
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()
