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
            .where(
                ClassGroup.id == class_id,
                ClassGroup.is_active.is_(True),
                School.is_active.is_(True),
            )
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
    active_school = await session.scalar(
        select(School.id).where(
            School.id == school_id,
            School.is_active.is_(True),
        )
    )
    if active_school is None:
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
    """Join an active class as a student without restoring stale privileges."""
    if await get_class_with_school(session, class_id) is None:
        raise ValueError("کلاس موردنظر فعال نیست یا در دسترس قرار ندارد.")
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



async def connect_group_by_code(
    session: AsyncSession,
    *,
    code: str,
    chat_id: int,
    chat_title: str | None,
    actor: User,
    bot_is_admin: bool,
    actor_is_admin: bool,
    now: datetime | None = None,
) -> ClassGroup:
    """Atomically validate and consume a class group-link code.

    A valid code alone is not authority: the caller must be a class manager
    and an administrator of the target Telegram group, and the bot must also
    be an administrator there. Existing links are never silently overwritten.
    """
    if chat_id == 0:
        raise ValueError("شناسه گروه معتبر نیست.")
    if not actor.is_active:
        raise PermissionError("حساب کاربری شما غیرفعال است.")
    if not bot_is_admin:
        raise PermissionError("ربات باید در گروه مدیر باشد.")
    if not actor_is_admin:
        raise PermissionError("فقط مدیر یا مالک گروه می‌تواند آن را متصل کند.")

    normalized_code = code.strip().upper()
    if not normalized_code:
        raise ValueError("کد اتصال معتبر نیست.")

    class_group = await session.scalar(
        select(ClassGroup)
        .join(AcademicYear, ClassGroup.academic_year_id == AcademicYear.id)
        .join(School, AcademicYear.school_id == School.id)
        .where(
            ClassGroup.group_link_code_hash == hash_code(normalized_code),
            ClassGroup.is_active.is_(True),
            School.is_active.is_(True),
        )
        .with_for_update()
    )
    if class_group is None:
        raise ValueError("کد اتصال معتبر نیست یا قبلاً استفاده شده است.")

    if not await is_class_manager(session, actor, class_group.id):
        raise PermissionError("فقط مدیر همین کلاس می‌تواند کد اتصال را فعال کند.")

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("زمان بررسی کد باید دارای منطقه زمانی باشد.")
    expires_at = class_group.group_link_code_expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < current:
            raise ValueError("کد اتصال منقضی شده است. لطفاً کد جدید دریافت کنید.")

    conflict = await session.scalar(
        select(ClassGroup.id).where(
            ClassGroup.group_chat_id == chat_id,
            ClassGroup.id != class_group.id,
        )
    )
    if conflict is not None:
        raise ValueError("این گروه قبلاً به کلاس دیگری متصل شده است.")

    if class_group.group_chat_id is not None and class_group.group_chat_id != chat_id:
        raise ValueError(
            "این کلاس از قبل به گروه دیگری متصل است. ابتدا اتصال قبلی را لغو کنید."
        )

    class_group.group_chat_id = chat_id
    class_group.group_chat_title = (chat_title or "گروه کلاس")[:180]
    class_group.group_link_code_hash = None
    class_group.group_link_code_expires_at = None
    await session.flush()
    return class_group
