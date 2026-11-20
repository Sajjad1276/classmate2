"""Integration: homework lifecycle — create, self-approve block, approve, reject, list."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from classmate.core.enums import EntityType, RecordStatus, Role
from classmate.db.models import ClassMembership, ClassGroup, Homework, User
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
