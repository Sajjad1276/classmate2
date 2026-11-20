"""Phase-two learning workflow services."""

from datetime import date, datetime, timezone
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from classmate.core.enums import RecordStatus
from classmate.db.models import Attendance, ClassMembership, Exam, Grade, Homework, HomeworkSubmission, Poll, PollOption, PollResponse, Subject, User


async def approved_homeworks_for_student(session: AsyncSession, class_id: int):
    return list((await session.execute(
        select(Homework, Subject).join(Subject, Homework.subject_id == Subject.id)
        .where(Homework.class_id == class_id, Homework.status == RecordStatus.APPROVED)
        .order_by(Homework.due_on.asc().nullslast(), Homework.created_at.desc()).limit(30)
    )).all())


async def save_submission(session: AsyncSession, homework_id: int, student_id: int, text: str | None = None, file_id: str | None = None, file_type: str | None = None) -> HomeworkSubmission:
    homework = await session.get(Homework, homework_id)
    if homework is None or homework.status != RecordStatus.APPROVED:
        raise ValueError("این تکلیف در دسترس نیست.")
    item = await session.scalar(select(HomeworkSubmission).where(HomeworkSubmission.homework_id == homework_id, HomeworkSubmission.student_id == student_id))
    if item is None:
        item = HomeworkSubmission(homework_id=homework_id, student_id=student_id, text=text, file_id=file_id, file_type=file_type)
        session.add(item)
    else:
        item.text, item.file_id, item.file_type = text, file_id, file_type
        item.status = "submitted"
        item.feedback = None
    await session.flush()
    return item


async def student_grades(session: AsyncSession, student_id: int):
    return list((await session.execute(
        select(Grade, Subject).join(Subject, Grade.subject_id == Subject.id)
        .where(Grade.student_id == student_id).order_by(Grade.created_at.desc()).limit(50)
    )).all())


async def student_attendance(session: AsyncSession, student_id: int, class_ids: list[int]):
    if not class_ids:
        return []
    return list((await session.execute(
        select(Attendance).where(Attendance.student_id == student_id, Attendance.class_id.in_(class_ids))
        .order_by(Attendance.attendance_date.desc()).limit(60)
    )).scalars().all())


async def active_polls(session: AsyncSession, class_ids: list[int]):
    if not class_ids:
        return []
    return list((await session.execute(
        select(Poll).where(Poll.class_id.in_(class_ids), Poll.is_active.is_(True))
        .order_by(Poll.created_at.desc()).limit(20)
    )).scalars().all())


async def poll_options(session: AsyncSession, poll_id: int):
    return list((await session.execute(select(PollOption).where(PollOption.poll_id == poll_id).order_by(PollOption.position))).scalars().all())


async def answer_poll(session: AsyncSession, poll_id: int, user_id: int, option_id: int) -> bool:
    poll = await session.get(Poll, poll_id)
    if poll is None or not poll.is_active or (poll.closes_at and poll.closes_at < datetime.now(timezone.utc)):
        return False
    existing = await session.scalar(select(PollResponse).where(PollResponse.poll_id == poll_id, PollResponse.user_id == user_id))
    if existing:
        existing.option_id = option_id
    else:
        session.add(PollResponse(poll_id=poll_id, option_id=option_id, user_id=user_id))
    await session.flush()
    return True


async def poll_result(session: AsyncSession, poll_id: int):
    return list((await session.execute(
        select(PollOption.label, func.count(PollResponse.id)).outerjoin(PollResponse, PollResponse.option_id == PollOption.id)
        .where(PollOption.poll_id == poll_id).group_by(PollOption.id, PollOption.label).order_by(PollOption.position)
    )).all())
