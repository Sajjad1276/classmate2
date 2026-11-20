"""Phase-two learning workflow services."""

from datetime import datetime, timezone
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from classmate.core.enums import RecordStatus
from classmate.db.models import Attendance, ClassMembership, Grade, Homework, HomeworkSubmission, Poll, PollOption, PollResponse, Subject, User
from classmate.services.permissions import user_can_access_class


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
    student = await session.get(User, student_id)
    membership_id = await session.scalar(
        select(ClassMembership.id).where(
            ClassMembership.class_id == homework.class_id,
            ClassMembership.user_id == student_id,
            ClassMembership.is_active.is_(True),
        )
    )
    if (
        student is None
        or not student.is_active
        or membership_id is None
        or not await user_can_access_class(session, student, homework.class_id)
    ):
        raise PermissionError("فقط عضو فعال کلاس می‌تواند پاسخ تکلیف را ارسال کند.")
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
    now = datetime.now(timezone.utc)
    return list((await session.execute(
        select(Poll).where(
            Poll.class_id.in_(class_ids),
            Poll.is_active.is_(True),
            (Poll.closes_at.is_(None)) | (Poll.closes_at > now),
        ).order_by(Poll.created_at.desc()).limit(20)
    )).scalars().all())


async def poll_options(session: AsyncSession, poll_id: int):
    return list((await session.execute(select(PollOption).where(PollOption.poll_id == poll_id).order_by(PollOption.position))).scalars().all())


async def answer_poll(session: AsyncSession, poll_id: int, user_id: int, option_id: int) -> bool:
    """Validate poll access and upsert one response atomically."""
    poll = await session.scalar(
        select(Poll).where(Poll.id == poll_id).with_for_update()
    )
    if poll is None or not poll.is_active:
        return False
    now = datetime.now(timezone.utc)
    if poll.closes_at is not None:
        closes_at = poll.closes_at
        if closes_at.tzinfo is None:
            closes_at = closes_at.replace(tzinfo=timezone.utc)
        if closes_at < now:
            return False

    user = await session.get(User, user_id)
    membership_id = await session.scalar(
        select(ClassMembership.id).where(
            ClassMembership.class_id == poll.class_id,
            ClassMembership.user_id == user_id,
            ClassMembership.is_active.is_(True),
        )
    )
    if (
        user is None
        or not user.is_active
        or membership_id is None
        or not await user_can_access_class(session, user, poll.class_id)
    ):
        return False
    option = await session.scalar(
        select(PollOption.id).where(
            PollOption.id == option_id,
            PollOption.poll_id == poll_id,
        )
    )
    if option is None:
        return False

    values = {
        "poll_id": poll_id,
        "option_id": option_id,
        "user_id": user_id,
        "created_at": now,
    }
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        statement = pg_insert(PollResponse).values(**values).on_conflict_do_update(
            index_elements=[PollResponse.poll_id, PollResponse.user_id],
            set_={"option_id": option_id, "created_at": now},
        )
        await session.execute(statement)
    elif dialect == "sqlite":
        statement = sqlite_insert(PollResponse).values(**values).on_conflict_do_update(
            index_elements=[PollResponse.poll_id, PollResponse.user_id],
            set_={"option_id": option_id, "created_at": now},
        )
        await session.execute(statement)
    else:
        existing = await session.scalar(select(PollResponse).where(
            PollResponse.poll_id == poll_id,
            PollResponse.user_id == user_id,
        ).with_for_update())
        if existing is None:
            session.add(PollResponse(**values))
        else:
            existing.option_id = option_id
            existing.created_at = now
    await session.flush()
    return True

async def poll_result(session: AsyncSession, poll_id: int):
    return list((await session.execute(
        select(PollOption.label, func.count(PollResponse.id))
        .outerjoin(
            PollResponse,
            (PollResponse.option_id == PollOption.id)
            & (PollResponse.poll_id == poll_id),
        )
        .where(PollOption.poll_id == poll_id)
        .group_by(PollOption.id, PollOption.label)
        .order_by(PollOption.position)
    )).all())
