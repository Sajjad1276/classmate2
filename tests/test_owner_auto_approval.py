"""Owner-created school records are published without a second approval step."""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from classmate.core.enums import RecordStatus, Role
from classmate.db.models import ApprovalEvent, AuditLog, SchoolMembership, User
from classmate.services.records import (
    create_daily_report,
    create_exam,
    create_homework,
    list_exams,
    list_homework,
    list_reports,
)


@pytest.mark.asyncio
async def test_class_manager_records_are_auto_approved_and_published(world):
    session = world["session"]
    manager = world["manager"]
    class_group = world["class"]
    # Exercise the class-manager permission independently of school-admin access.
    school_membership = await session.scalar(
        select(SchoolMembership).where(
            SchoolMembership.school_id == world["school"].id,
            SchoolMembership.user_id == manager.id,
        )
    )
    school_membership.is_active = False

    homework = await create_homework(
        session,
        class_group.id,
        world["subject"],
        "تمرین مدیر",
        "توضیحات",
        date.today() + timedelta(days=5),
        manager.id,
    )
    exam = await create_exam(
        session,
        class_group.id,
        world["subject"],
        "آزمون مدیر",
        "فصل اول",
        "quiz",
        datetime.now(timezone.utc) + timedelta(days=7),
        manager.id,
    )
    report = await create_daily_report(
        session,
        class_group.id,
        date.today(),
        "گزارش ثبت‌شده توسط مدیر",
        manager.id,
    )

    for entity_type, record in (
        ("homework", homework),
        ("exam", exam),
        ("report", report),
    ):
        assert record.status == RecordStatus.APPROVED
        assert record.approved_by == manager.id
        assert record.approved_at is not None

        event = await session.scalar(
            select(ApprovalEvent).where(
                ApprovalEvent.entity_type == entity_type,
                ApprovalEvent.entity_id == record.id,
                ApprovalEvent.action == "AUTO_APPROVED",
            )
        )
        assert event is not None
        assert event.actor_id == manager.id

        audit_entry = await session.scalar(
            select(AuditLog).where(
                AuditLog.class_id == class_group.id,
                AuditLog.entity_id == record.id,
                AuditLog.action == "auto_approve",
            )
        )
        assert audit_entry is not None

    assert len(await list_homework(session, class_group.id)) == 1
    assert len(await list_exams(session, class_group.id)) == 1
    assert len(await list_reports(session, class_group.id, date.today())) == 1


@pytest.mark.asyncio
async def test_school_admin_can_publish_without_class_membership(world):
    session = world["session"]
    owner = User(
        telegram_id=2004,
        username="school_owner",
        full_name="مالک مدرسه",
        private_chat_started=True,
    )
    session.add(owner)
    await session.flush()
    session.add(
        SchoolMembership(
            school_id=world["school"].id,
            user_id=owner.id,
            role=Role.SCHOOL_ADMIN,
            is_active=True,
        )
    )
    await session.flush()

    homework = await create_homework(
        session,
        world["class"].id,
        world["subject"],
        "تکلیف مالک مدرسه",
        "توضیحات",
        date.today() + timedelta(days=4),
        owner.id,
    )

    assert homework.status == RecordStatus.APPROVED
    assert homework.approved_by == owner.id
    assert homework.approved_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("creator_key", ["student", "approver"])
async def test_non_owner_created_records_still_require_approval(world, creator_key):
    session = world["session"]
    creator = world[creator_key]

    homework = await create_homework(
        session,
        world["class"].id,
        world["subject"],
        "تکلیف نیازمند بررسی",
        "توضیحات",
        None,
        creator.id,
    )
    exam = await create_exam(
        session,
        world["class"].id,
        world["subject"],
        "امتحان نیازمند بررسی",
        "فصل اول",
        "quiz",
        datetime.now(timezone.utc) + timedelta(days=3),
        creator.id,
    )
    report = await create_daily_report(
        session,
        world["class"].id,
        date.today() - timedelta(days=1 if creator_key == "student" else 2),
        "گزارش نیازمند بررسی",
        creator.id,
    )

    for record in (homework, exam, report):
        assert record.status == RecordStatus.PENDING
        assert record.approved_by is None
        assert record.approved_at is None
