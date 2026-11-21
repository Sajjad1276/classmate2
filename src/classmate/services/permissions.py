"""Learner access and invitation-code helpers."""

import hashlib
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from classmate.db.models import AcademicYear, ClassGroup, ClassMembership, School, User


def hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


async def get_class_with_school(session: AsyncSession, class_id: int):
    return (
        await session.execute(
            select(ClassGroup, AcademicYear, School)
            .join(AcademicYear, ClassGroup.academic_year_id == AcademicYear.id)
            .join(School, AcademicYear.school_id == School.id)
            .where(
                ClassGroup.id == class_id,
                ClassGroup.is_active.is_(True),
                School.is_active.is_(True),
            )
        )
    ).first()


async def user_can_access_class(session: AsyncSession, user: User, class_id: int) -> bool:
    if not user.is_active or await get_class_with_school(session, class_id) is None:
        return False
    return (
        await session.scalar(
            select(ClassMembership.id).where(
                ClassMembership.class_id == class_id,
                ClassMembership.user_id == user.id,
                ClassMembership.is_active.is_(True),
            )
        )
        is not None
    )


async def activate_student_membership(
    session: AsyncSession, class_id: int, user_id: int
) -> ClassMembership:
    if (
        await session.scalar(select(User.id).where(User.id == user_id, User.is_active.is_(True)))
        is None
    ):
        raise PermissionError("حساب کاربری فعال نیست.")
    if await get_class_with_school(session, class_id) is None:
        raise ValueError("کلاس موردنظر فعال نیست یا در دسترس قرار ندارد.")
    membership = await session.scalar(
        select(ClassMembership)
        .where(ClassMembership.class_id == class_id, ClassMembership.user_id == user_id)
        .with_for_update()
    )
    if membership is None:
        membership = ClassMembership(class_id=class_id, user_id=user_id, is_active=True)
        session.add(membership)
    else:
        membership.is_active = True
    await session.flush()
    return membership


def code_expiry(minutes: int = 30) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)
