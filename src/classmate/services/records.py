"""Read-only educational record queries. Writers live outside ClassMate 2."""

from datetime import date
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from classmate.core.enums import RecordStatus
from classmate.db.models import (
    AcademicYear,
    ClassGroup,
    DailyReport,
    Exam,
    Homework,
    School,
    Subject,
)


def normalize_subject_name(value: str) -> str:
    return " ".join(
        value.replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ").split()
    ).casefold()


async def get_group_context(session: AsyncSession, telegram_chat_id: int):
    return (
        await session.execute(
            select(ClassGroup, AcademicYear, School)
            .join(AcademicYear, ClassGroup.academic_year_id == AcademicYear.id)
            .join(School, AcademicYear.school_id == School.id)
            .where(
                ClassGroup.group_chat_id == telegram_chat_id,
                ClassGroup.is_active.is_(True),
                School.is_active.is_(True),
            )
        )
    ).first()


async def list_homework(
    session: AsyncSession, class_id: int, subject_name: str | None = None
) -> list[tuple[Homework, Subject]]:
    query = (
        select(Homework, Subject)
        .join(Subject, Homework.subject_id == Subject.id)
        .where(Homework.class_id == class_id, Homework.status == RecordStatus.APPROVED)
        .order_by(Homework.due_on.asc().nullslast(), Homework.created_at.desc())
        .limit(30)
    )
    if subject_name:
        query = query.where(Subject.normalized_name == normalize_subject_name(subject_name))
    return list((await session.execute(query)).all())


async def list_exams(session: AsyncSession, class_id: int) -> list[tuple[Exam, Subject]]:
    return list(
        (
            await session.execute(
                select(Exam, Subject)
                .join(Subject, Exam.subject_id == Subject.id)
                .where(Exam.class_id == class_id, Exam.status == RecordStatus.APPROVED)
                .order_by(Exam.scheduled_at.asc())
                .limit(30)
            )
        ).all()
    )


async def list_reports(session: AsyncSession, class_id: int, report_date: date):
    return list(
        (
            await session.execute(
                select(DailyReport)
                .where(
                    DailyReport.class_id == class_id,
                    DailyReport.report_date == report_date,
                    DailyReport.status == RecordStatus.APPROVED,
                )
                .order_by(DailyReport.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
