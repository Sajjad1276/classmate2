"""Regression tests for authorization, voting and reminder integrity."""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from classmate.core.enums import EntityType, OutboxStatus, RecordStatus, ReminderStatus, Role
from classmate.db.models import (
    ClassGroup,
    ClassMembership,
    Homework,
    Poll,
    PollOption,
    PollResponse,
    ReminderJob,
    TelegramOutbox,
    User,
)
from classmate.services.phase1 import archive_record
from classmate.services.phase2 import answer_poll, save_submission
from classmate.services.permissions import (
    activate_student_membership,
    user_can_access_class,
)
from classmate.services.records import create_homework
from classmate.services.outbox import enqueue_telegram_message
from classmate.services.users import ensure_user


@pytest.mark.asyncio
async def test_rejoining_with_student_invite_never_restores_manager_role(world):
    session = world["session"]
    membership = await session.scalar(
        select(ClassMembership).where(
            ClassMembership.class_id == world["class"].id,
            ClassMembership.user_id == world["manager"].id,
        )
    )
    membership.is_active = False
    await session.flush()

    restored = await activate_student_membership(
        session, world["class"].id, world["manager"].id
    )

    assert restored.is_active is True
    assert restored.role == Role.STUDENT


@pytest.mark.asyncio
async def test_revoked_membership_cannot_access_class(world):
    session = world["session"]
    membership = await session.scalar(
        select(ClassMembership).where(
            ClassMembership.class_id == world["class"].id,
            ClassMembership.user_id == world["student"].id,
        )
    )
    membership.is_active = False
    await session.flush()

    assert not await user_can_access_class(
        session, world["student"], world["class"].id
    )


@pytest.mark.asyncio
async def test_inactive_user_cannot_be_resolved(world):
    user = world["student"]
    user.is_active = False
    await world["session"].flush()

    with pytest.raises(PermissionError, match="غیرفعال"):
        await ensure_user(
            world["session"],
            telegram_id=user.telegram_id,
            username=user.username,
            full_name=user.full_name,
            private_chat_started=True,
        )


@pytest.mark.asyncio
async def test_poll_rejects_foreign_option_and_non_member(world):
    session = world["session"]
    first = Poll(
        class_id=world["class"].id,
        question="نظرسنجی اول",
        created_by=world["manager"].id,
    )
    second = Poll(
        class_id=world["class"].id,
        question="نظرسنجی دوم",
        created_by=world["manager"].id,
    )
    session.add_all([first, second])
    await session.flush()

    first_option = PollOption(poll_id=first.id, label="الف", position=0)
    foreign_option = PollOption(poll_id=second.id, label="ب", position=0)
    outsider = User(telegram_id=1004, full_name="کاربر خارج از کلاس")
    session.add_all([first_option, foreign_option, outsider])
    await session.flush()

    assert not await answer_poll(
        session, first.id, world["student"].id, foreign_option.id
    )
    assert await answer_poll(session, first.id, world["student"].id, first_option.id)
    assert not await answer_poll(session, first.id, outsider.id, first_option.id)

    responses = list(
        (
            await session.scalars(
                select(PollResponse).where(PollResponse.poll_id == first.id)
            )
        ).all()
    )
    assert len(responses) == 1
    assert responses[0].option_id == first_option.id


@pytest.mark.asyncio
async def test_submission_requires_active_class_membership(world):
    session = world["session"]
    homework = Homework(
        class_id=world["class"].id,
        subject_id=world["subject"].id,
        title="تکلیف",
        description="توضیح",
        due_on=date.today() + timedelta(days=2),
        status=RecordStatus.APPROVED,
        created_by=world["manager"].id,
    )
    session.add(homework)
    await session.flush()

    outsider = User(telegram_id=2005, full_name="کاربر خارج از کلاس")
    session.add(outsider)
    await session.flush()

    with pytest.raises(PermissionError, match="عضو فعال"):
        await save_submission(
            session,
            homework.id,
            outsider.id,
            text="پاسخ کاربر خارج از کلاس",
        )


@pytest.mark.asyncio
async def test_archiving_homework_cancels_pending_reminders(world):
    session = world["session"]
    homework = Homework(
        class_id=world["class"].id,
        subject_id=world["subject"].id,
        title="تکلیف آرشیوی",
        description="توضیح",
        due_on=date.today() + timedelta(days=2),
        status=RecordStatus.APPROVED,
        created_by=world["manager"].id,
    )
    session.add(homework)
    await session.flush()

    reminder = ReminderJob(
        class_id=world["class"].id,
        entity_type=EntityType.HOMEWORK.value,
        entity_id=homework.id,
        target_type="private",
        target_id=world["student"].telegram_id,
        message_text="یادآوری",
        scheduled_at=datetime.now(timezone.utc),
        status=ReminderStatus.PENDING,
        dedupe_key=f"test-homework-{homework.id}",
    )
    outbox = await enqueue_telegram_message(
        session,
        target_type="private",
        target_chat_id=world["student"].telegram_id,
        recipient_user_id=world["student"].id,
        class_id=world["class"].id,
        entity_type="homework",
        entity_id=homework.id,
        expected_record_status="APPROVED",
        text="بازخورد تکلیف",
        idempotency_key=f"feedback:{homework.id}",
    )
    session.add(reminder)
    await session.flush()

    ok, _ = await archive_record(
        session, EntityType.HOMEWORK.value, homework.id, world["manager"]
    )

    assert ok is True
    assert homework.status == RecordStatus.CANCELLED
    assert reminder.status == ReminderStatus.CANCELLED
    assert outbox.status == OutboxStatus.CANCELLED


@pytest.mark.asyncio
async def test_homework_rejects_subject_not_linked_to_class(world):
    session = world["session"]
    second_class = ClassGroup(
        academic_year_id=world["year"].id,
        name="دهم ۱۰۲",
        grade="دهم",
        section="۱۰۲",
    )
    session.add(second_class)
    await session.flush()

    with pytest.raises(ValueError, match="متعلق به این کلاس نیست"):
        await create_homework(
            session,
            second_class.id,
            world["subject"],
            "نباید ثبت شود",
            "درس به کلاس دوم وصل نشده است",
            None,
            world["manager"].id,
        )


@pytest.mark.asyncio
async def test_stale_membership_cannot_access_inactive_class(world):
    session = world["session"]
    world["class"].is_active = False
    await session.flush()

    assert not await user_can_access_class(
        session, world["student"], world["class"].id
    )



@pytest.mark.asyncio
async def test_telegram_outbox_enqueue_is_idempotent_and_detects_key_collisions(world):
    session = world["session"]
    world["class"].group_chat_id = -1001234567890
    args = {
        "target_type": "group",
        "target_chat_id": world["class"].group_chat_id,
        "class_id": world["class"].id,
        "entity_type": "homework",
        "entity_id": 321,
        "expected_record_status": "PENDING",
        "text": "درخواست جدید برای بررسی",
        "action_type": "homework",
        "action_id": 321,
        "idempotency_key": "approval:homework:321",
    }

    first = await enqueue_telegram_message(session, **args)
    second = await enqueue_telegram_message(session, **args)

    assert first.id == second.id
    assert first.status == OutboxStatus.PENDING
    assert await session.scalar(
        select(TelegramOutbox.id).where(
            TelegramOutbox.idempotency_key == "approval:homework:321"
        )
    ) == first.id

    with pytest.raises(ValueError, match="reused with a different payload"):
        await enqueue_telegram_message(
            session, **{**args, "text": "متن دیگری با همان کلید"}
        )
