"""Phase-two learning workflow tests."""

from datetime import date, timedelta

import pytest

from classmate.core.enums import EntityType
from classmate.db.models import Attendance, Grade
from classmate.services.phase2 import save_submission, student_attendance, student_grades
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
