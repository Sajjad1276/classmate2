"""Integration: homework lifecycle — create, self-approve block, approve, reject, list."""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from classmate.core.enums import EntityType, OutboxStatus, RecordStatus, ReminderStatus, Role
from classmate.db.models import ClassMembership, ClassGroup, Homework, ReminderJob, TelegramOutbox, User
from classmate.services.permissions import (
    can_approve,
    code_expiry,
    hash_code,
    is_class_manager,
    make_code,
    user_can_access_class,
)
from classmate.services.records import (
    approve_record,
    create_homework,
    list_homework,
    list_pending_records,
    reject_record,
)
from classmate.services.phase1 import update_record
from classmate.services.users import ensure_user


@pytest.mark.asyncio
async def test_create_homework_starts_pending(world):
    session = world["session"]
    hw = await create_homework(
        session,
        world["class"].id,
        world["subject"],
        "تمرین ۱",
        "صفحه ۲۰",
        date.today() + timedelta(days=3),
        world["student"].id,
    )
    assert hw.id is not None
    assert hw.status == RecordStatus.PENDING
    assert hw.created_by == world["student"].id


@pytest.mark.asyncio
async def test_list_homework_hides_pending(world):
    session = world["session"]
    await create_homework(
        session,
        world["class"].id,
        world["subject"],
        "پنهان",
        "توضیح",
        None,
        world["student"].id,
    )
    rows = await list_homework(session, world["class"].id)
    assert rows == []


@pytest.mark.asyncio
async def test_self_approve_blocked(world):
    session = world["session"]
    student = world["student"]
    hw = await create_homework(
        session,
        world["class"].id,
        world["subject"],
        "خودتأییدی",
        "نباید شود",
        None,
        student.id,
    )
    ok, msg = await approve_record(
        session,
        EntityType.HOMEWORK.value,
        hw.id,
        student.id,
        world["school"].id,
        world["class"].id,
        "Asia/Tehran",
    )
    assert ok is False
    assert "ثبت‌کننده" in msg
    await session.refresh(hw)
    assert hw.status == RecordStatus.PENDING


@pytest.mark.asyncio
async def test_approver_can_approve(world):
    session = world["session"]
    hw = await create_homework(
        session,
        world["class"].id,
        world["subject"],
        "برای تأیید",
        "توضیح",
        date.today() + timedelta(days=2),
        world["student"].id,
    )
    ok, msg = await approve_record(
        session,
        EntityType.HOMEWORK.value,
        hw.id,
        world["approver"].id,
        world["school"].id,
        world["class"].id,
        "Asia/Tehran",
    )
    assert ok is True
    await session.refresh(hw)
    assert hw.status == RecordStatus.APPROVED
    assert hw.approved_by == world["approver"].id
    assert hw.approved_at is not None

    rows = await list_homework(session, world["class"].id)
    assert len(rows) == 1
    assert rows[0][0].id == hw.id
    assert rows[0][1].name == "ریاضی"


@pytest.mark.asyncio
async def test_reject_then_not_listable(world):
    session = world["session"]
    hw = await create_homework(
        session,
        world["class"].id,
        world["subject"],
        "رد شود",
        "x",
        None,
        world["student"].id,
    )
    ok, _ = await reject_record(
        session,
        EntityType.HOMEWORK.value,
        hw.id,
        world["manager"].id,
        world["school"].id,
        world["class"].id,
        note="ناکافی",
    )
    assert ok is True
    await session.refresh(hw)
    assert hw.status == RecordStatus.REJECTED
    assert await list_homework(session, world["class"].id) == []


@pytest.mark.asyncio
async def test_double_approve_fails(world):
    session = world["session"]
    hw = await create_homework(
        session,
        world["class"].id,
        world["subject"],
        "دوبار",
        "x",
        None,
        world["student"].id,
    )
    ok1, _ = await approve_record(
        session,
        EntityType.HOMEWORK.value,
        hw.id,
        world["approver"].id,
        world["school"].id,
        world["class"].id,
        "Asia/Tehran",
    )
    assert ok1 is True
    ok2, msg = await approve_record(
        session,
        EntityType.HOMEWORK.value,
        hw.id,
        world["manager"].id,
        world["school"].id,
        world["class"].id,
        "Asia/Tehran",
    )
    assert ok2 is False
    assert "وضعیت" in msg or "قابل تأیید" in msg


@pytest.mark.asyncio
async def test_pending_list_only_pending(world):
    session = world["session"]
    pending_hw = await create_homework(
        session,
        world["class"].id,
        world["subject"],
        "pending",
        "x",
        None,
        world["student"].id,
    )
    approved = await create_homework(
        session,
        world["class"].id,
        world["subject"],
        "approved",
        "x",
        None,
        world["student"].id,
    )
    await approve_record(
        session,
        EntityType.HOMEWORK.value,
        approved.id,
        world["approver"].id,
        world["school"].id,
        world["class"].id,
        "Asia/Tehran",
    )
    pending = await list_pending_records(session, world["class"].id)
    ids = {rec.id for _, rec in pending}
    assert pending_hw.id in ids
    assert approved.id not in ids


@pytest.mark.asyncio
async def test_permissions_roles(world):
    session = world["session"]
    cls_id = world["class"].id
    assert await can_approve(session, world["approver"], cls_id) is True
    assert await can_approve(session, world["manager"], cls_id) is True
    assert await can_approve(session, world["student"], cls_id) is False
    assert await is_class_manager(session, world["manager"], cls_id) is True
    assert await is_class_manager(session, world["student"], cls_id) is False
    assert await user_can_access_class(session, world["student"], cls_id) is True


@pytest.mark.asyncio
async def test_join_with_member_code(world):
    """Simulate /join flow: set join code, new user joins as student."""
    session = world["session"]
    cls: ClassGroup = world["class"]
    code = make_code(8)
    cls.member_join_code_hash = hash_code(code)
    cls.member_join_code_expires_at = code_expiry(60 * 24)
    await session.flush()

    newcomer = await ensure_user(
        session,
        telegram_id=9001,
        username="new",
        full_name="عضو جدید",
        private_chat_started=True,
    )

    # Lookup like wizard
    found = await session.scalar(
        select(ClassGroup).where(
            ClassGroup.member_join_code_hash == hash_code(code),
            ClassGroup.is_active.is_(True),
        )
    )
    assert found is not None
    assert found.id == cls.id

    session.add(
        ClassMembership(class_id=cls.id, user_id=newcomer.id, role=Role.STUDENT)
    )
    await session.flush()

    assert await user_can_access_class(session, newcomer, cls.id) is True
    assert await can_approve(session, newcomer, cls.id) is False


@pytest.mark.asyncio
async def test_join_wrong_code_fails(world):
    session = world["session"]
    cls = world["class"]
    cls.member_join_code_hash = hash_code("RIGHTCOD")
    cls.member_join_code_expires_at = code_expiry(60)
    await session.flush()

    found = await session.scalar(
        select(ClassGroup).where(
            ClassGroup.member_join_code_hash == hash_code("WRONGCOD"),
            ClassGroup.is_active.is_(True),
        )
    )
    assert found is None


@pytest.mark.asyncio
async def test_expired_join_code(world):
    from datetime import datetime, timezone, timedelta

    session = world["session"]
    cls = world["class"]
    code = "EXPIRED1"
    cls.member_join_code_hash = hash_code(code)
    cls.member_join_code_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await session.flush()

    found = await session.scalar(
        select(ClassGroup).where(ClassGroup.member_join_code_hash == hash_code(code))
    )
    assert found is not None
    assert found.member_join_code_expires_at < datetime.now(timezone.utc)



@pytest.mark.asyncio
async def test_record_services_reject_unauthorized_approver(world):
    session = world["session"]
    homework = await create_homework(
        session,
        world["class"].id,
        world["subject"],
        "رکورد محرمانه",
        "شرح",
        None,
        world["manager"].id,
    )

    ok, message = await approve_record(
        session,
        EntityType.HOMEWORK.value,
        homework.id,
        world["student"].id,
        world["school"].id,
        world["class"].id,
        "Asia/Tehran",
    )
    assert ok is False
    assert "اجازه تأیید" in message
    await session.refresh(homework)
    assert homework.status == RecordStatus.PENDING

    rejected, message = await reject_record(
        session,
        EntityType.HOMEWORK.value,
        homework.id,
        world["student"].id,
        world["school"].id,
        world["class"].id,
        note="تلاش غیرمجاز",
    )
    assert rejected is False
    assert "اجازه رد" in message
    await session.refresh(homework)
    assert homework.status == RecordStatus.PENDING



@pytest.mark.asyncio
async def test_editing_title_updates_queued_reminders_and_approval_messages(world):
    session = world["session"]
    homework = await create_homework(
        session,
        world["class"].id,
        world["subject"],
        "عنوان قدیمی",
        "توضیح",
        date.today() + timedelta(days=3),
        world["student"].id,
    )
    approved, _ = await approve_record(
        session,
        EntityType.HOMEWORK.value,
        homework.id,
        world["approver"].id,
        world["school"].id,
        world["class"].id,
        "Asia/Tehran",
    )
    assert approved is True

    now = datetime.now(timezone.utc)
    reminder = ReminderJob(
        class_id=world["class"].id,
        entity_type=EntityType.HOMEWORK.value,
        entity_id=homework.id,
        target_type="group",
        target_id=-1001234567890,
        message_text="یادآوری تکلیف: عنوان قدیمی",
        scheduled_at=now + timedelta(hours=1),
        status=ReminderStatus.PENDING,
        dedupe_key=f"edit-title-reminder:{homework.id}",
    )
    queued = TelegramOutbox(
        target_type="group",
        target_chat_id=-1001234567890,
        class_id=world["class"].id,
        entity_type=EntityType.HOMEWORK.value,
        entity_id=homework.id,
        expected_record_status=RecordStatus.APPROVED.value,
        text="تکلیف جدید برای بررسی: عنوان قدیمی",
        scheduled_at=now + timedelta(hours=1),
        status=OutboxStatus.PENDING,
        idempotency_key=f"edit-title-outbox:{homework.id}",
    )
    session.add_all([reminder, queued])
    await session.flush()

    ok, message = await update_record(
        session,
        EntityType.HOMEWORK.value,
        homework.id,
        "عنوان تازه",
        "توضیحات تازه",
        world["manager"],
    )
    assert ok is True
    assert "موفقیت" in message
    await session.refresh(reminder)
    await session.refresh(queued)
    assert reminder.message_text == "یادآوری تکلیف: عنوان تازه"
    assert queued.text == "تکلیف جدید برای بررسی: عنوان تازه"


@pytest.mark.asyncio
async def test_editing_record_rejects_blank_title(world):
    homework = await create_homework(
        world["session"],
        world["class"].id,
        world["subject"],
        "عنوان",
        "توضیح",
        None,
        world["student"].id,
    )
    ok, message = await update_record(
        world["session"],
        EntityType.HOMEWORK.value,
        homework.id,
        "   ",
        "توضیح",
        world["manager"],
    )
    assert ok is False
    assert "خالی" in message
