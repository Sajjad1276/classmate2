"""Authorization and membership checks."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.core.enums import Role
from classmate.db.models import (
    AcademicYear,
    ClassGroup,
    ClassMembership,
    School,
    SchoolMembership,
    User,
)


def make_code(length: int = 8) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


async def get_class_with_school(
    session: AsyncSession, class_id: int
) -> tuple[ClassGroup, AcademicYear, School] | None:
    row = (
        await session.execute(
            select(ClassGroup, AcademicYear, School)
            .join(AcademicYear, ClassGroup.academic_year_id == AcademicYear.id)
            .join(School, AcademicYear.school_id == School.id)
            .where(ClassGroup.id == class_id, ClassGroup.is_active.is_(True))
        )
    ).first()
    return row  # type: ignore[return-value]


async def user_can_access_class(session: AsyncSession, user: User, class_id: int) -> bool:
    if not user.is_active:
        return False
    # Validate the resource itself before considering any role or membership.
    context = await get_class_with_school(session, class_id)
    if context is None:
        return False
    if user.is_platform_admin:
        return True

    membership = await session.scalar(
        select(ClassMembership).where(
            ClassMembership.class_id == class_id,
            ClassMembership.user_id == user.id,
            ClassMembership.is_active.is_(True),
        )
    )
    if membership:
        return True

    _, _, school = context
    school_admin = await session.scalar(
        select(SchoolMembership).where(
            SchoolMembership.school_id == school.id,
            SchoolMembership.user_id == user.id,
            SchoolMembership.role == Role.SCHOOL_ADMIN,
            SchoolMembership.is_active.is_(True),
        )
    )
    return school_admin is not None
async def is_school_admin(session: AsyncSession, user: User, school_id: int) -> bool:
    if not user.is_active:
        return False
    if user.is_platform_admin:
        return True
    return (
        await session.scalar(
            select(SchoolMembership.id).where(
                SchoolMembership.school_id == school_id,
                SchoolMembership.user_id == user.id,
                SchoolMembership.role == Role.SCHOOL_ADMIN,
                SchoolMembership.is_active.is_(True),
            )
        )
    ) is not None


async def can_approve(session: AsyncSession, user: User, class_id: int) -> bool:
    if not user.is_active or await get_class_with_school(session, class_id) is None:
        return False
    if user.is_platform_admin:
        return True

    membership = await session.scalar(
        select(ClassMembership).where(
            ClassMembership.class_id == class_id,
            ClassMembership.user_id == user.id,
            ClassMembership.is_active.is_(True),
        )
    )
    if membership and membership.role in {Role.CLASS_MANAGER, Role.TRUSTED_APPROVER}:
        return True

    context = await get_class_with_school(session, class_id)
    if context is None:
        return False
    return await is_school_admin(session, user, context[2].id)


async def is_class_manager(session: AsyncSession, user: User, class_id: int) -> bool:
    if not user.is_active or await get_class_with_school(session, class_id) is None:
        return False
    if user.is_platform_admin:
        return True
    membership = await session.scalar(
        select(ClassMembership).where(
            ClassMembership.class_id == class_id,
            ClassMembership.user_id == user.id,
            ClassMembership.role == Role.CLASS_MANAGER,
            ClassMembership.is_active.is_(True),
        )
    )
    if membership:
        return True
    context = await get_class_with_school(session, class_id)
    if context is None:
        return False
    return await is_school_admin(session, user, context[2].id)


async def activate_student_membership(
    session: AsyncSession, class_id: int, user_id: int
) -> ClassMembership:
    """Join as a student without restoring stale privileged roles."""
    membership = await session.scalar(
        select(ClassMembership).where(
            ClassMembership.class_id == class_id,
            ClassMembership.user_id == user_id,
        ).with_for_update()
    )
    if membership is None:
        membership = ClassMembership(
            class_id=class_id, user_id=user_id, role=Role.STUDENT, is_active=True
        )
        session.add(membership)
    elif not membership.is_active:
        membership.role = Role.STUDENT
        membership.is_active = True
    await session.flush()
    return membership


def code_expiry(minutes: int = 30) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)
