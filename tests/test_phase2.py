"""Phase-two learning workflow tests."""

from datetime import date, timedelta

import pytest

from classmate.core.enums import EntityType
from sqlalchemy import func, select
from classmate.db.models import Attendance, Grade
from classmate.services.phase2 import save_attendance, save_submission, student_attendance, student_grades
from classmate.services.records import approve_record, create_homework


@pytest.mark.asyncio
async def test_student_submission_can_be_updated(world):
    session = world["session"]
    homework = await create_homework(
        session, world["class"].id, world["subject"], "تمرین فاز دو", "پاسخ دهید",
        date.today() + timedelta(days=2), world["student"].id,
    )
    await approve_record(session, EntityType.HOMEWORK.value, homework.id, world["approver"].id, world["school"].id, world["class"].id, "Asia/Tehran")
    first = await save_submission(session, homework.id, world["student"].id, text="پاسخ اول")
    second = await save_submission(session, homework.id, world["student"].id, text="پاسخ اصلاح‌شده", file_id="telegram-file", file_type="document")
    assert first.id == second.id
    assert second.text == "پاسخ اصلاح‌شده"
    assert second.file_type == "document"


@pytest.mark.asyncio
async def test_student_grade_and_attendance_views(world):
    session = world["session"]
    session.add(Grade(class_id=world["class"].id, student_id=world["student"].id, subject_id=world["subject"].id, source_type="manual", score=18, max_score=20, feedback="خوب", graded_by=world["manager"].id))
    session.add(Attendance(class_id=world["class"].id, student_id=world["student"].id, attendance_date=date.today(), status="present", marked_by=world["manager"].id))
    await session.flush()
    grades = await student_grades(session, world["student"].id, [world["class"].id])
    attendance = await student_attendance(session, world["student"].id, [world["class"].id])
    assert len(grades) == 1
    assert grades[0][0].score == 18
    assert attendance[0].status == "present"



@pytest.mark.asyncio
async def test_attendance_upsert_is_idempotent_and_requires_manager(world):
    session = world["session"]
    day = date.today()
    first = await save_attendance(
        session,
        class_id=world["class"].id,
        student_id=world["student"].id,
        attendance_date=day,
        status="present",
        marked_by=world["manager"].id,
        note="ثبت اولیه",
    )
    second = await save_attendance(
        session,
        class_id=world["class"].id,
        student_id=world["student"].id,
        attendance_date=day,
        status="late",
        marked_by=world["manager"].id,
        note="اصلاح وضعیت",
    )

    assert first.id == second.id
    assert second.status == "late"
    assert second.note == "اصلاح وضعیت"
    count = await session.scalar(
        select(func.count()).select_from(Attendance).where(
            Attendance.class_id == world["class"].id,
            Attendance.student_id == world["student"].id,
            Attendance.attendance_date == day,
        )
    )
    assert count == 1

    with pytest.raises(PermissionError, match="فقط مدیر کلاس"):
        await save_attendance(
            session,
            class_id=world["class"].id,
            student_id=world["student"].id,
            attendance_date=day,
            status="present",
            marked_by=world["student"].id,
        )


@pytest.mark.asyncio
async def test_attendance_upsert_rejects_invalid_status(world):
    with pytest.raises(ValueError, match="وضعیت"):
        await save_attendance(
            world["session"],
            class_id=world["class"].id,
            student_id=world["student"].id,
            attendance_date=date.today(),
            status="unknown",
            marked_by=world["manager"].id,
        )
