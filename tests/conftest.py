"""Shared fixtures — in-memory SQLite for service integration tests."""

import os

# Must be set before classmate.core.config is imported anywhere.
os.environ.setdefault("BOT_TOKEN", "0000000000:TEST_TOKEN_FOR_TESTS_ONLY")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("BOOTSTRAP_ADMIN_IDS", "")

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from classmate.core.enums import Role
from classmate.db.base import Base
from classmate.db.models import (
    AcademicYear,
    ClassGroup,
    ClassMembership,
    ClassSubject,
    School,
    SchoolMembership,
    Subject,
    User,
)


@pytest.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as sess:
        yield sess


@pytest.fixture
async def world(session: AsyncSession):
    """Minimal school → year → class → subject + three users."""
    school = School(name="دبیرستان آزمون", timezone="Asia/Tehran")
    session.add(school)
    await session.flush()

    year = AcademicYear(school_id=school.id, name="۱۴۰۵-۱۴۰۶", is_current=True)
    session.add(year)
    await session.flush()

    cls = ClassGroup(
        academic_year_id=year.id,
        name="دهم ۱۰۱",
        grade="دهم",
        section="۱۰۱",
    )
    session.add(cls)
    await session.flush()

    subject = Subject(school_id=school.id, name="ریاضی", normalized_name="ریاضی")
    session.add(subject)
    await session.flush()
    session.add(ClassSubject(class_id=cls.id, subject_id=subject.id))

    student = User(telegram_id=1001, username="student", full_name="دانش‌آموز", private_chat_started=True)
    manager = User(telegram_id=1002, username="manager", full_name="مدیر کلاس", private_chat_started=True)
    approver = User(telegram_id=1003, username="approver", full_name="تأییدکننده", private_chat_started=True)
    session.add_all([student, manager, approver])
    await session.flush()

    session.add_all(
        [
            ClassMembership(class_id=cls.id, user_id=student.id, role=Role.STUDENT),
            ClassMembership(class_id=cls.id, user_id=manager.id, role=Role.CLASS_MANAGER),
            ClassMembership(class_id=cls.id, user_id=approver.id, role=Role.TRUSTED_APPROVER),
            SchoolMembership(school_id=school.id, user_id=manager.id, role=Role.SCHOOL_ADMIN),
        ]
    )
    await session.flush()

    return {
        "session": session,
        "school": school,
        "year": year,
        "class": cls,
        "subject": subject,
        "student": student,
        "manager": manager,
        "approver": approver,
    }
