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
        # Rejoining with a student code must never restore elevated class roles.
        membership.role = Role.STUDENT
    await session.flush()
    return membership


def code_expiry(minutes: int = 30) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


async def connect_group_by_code(
    session: AsyncSession,
    *,
    code: str,
    chat_id: int,
    chat_title: str,
    actor: User,
    bot_is_admin: bool,
    actor_is_admin: bool,
) -> ClassGroup:
    """Consume a one-time group link code after validating both admin scopes."""
    if not actor.is_active:
        raise PermissionError("حساب کاربری فعال نیست.")
    if not actor_is_admin:
        raise PermissionError("فقط مدیر یا مالک گروه می‌تواند آن را متصل کند.")
    if not bot_is_admin:
        raise PermissionError("ربات باید در گروه مدیر باشد تا اتصال امن انجام شود.")
    if chat_id >= 0:
        raise ValueError("فقط گروه یا سوپرگروه تلگرام قابل اتصال است.")

    normalized_title = " ".join(chat_title.split())
    if not normalized_title:
        normalized_title = str(chat_id)
    if len(normalized_title) > 180:
        normalized_title = normalized_title[:180]

    now = datetime.now(timezone.utc)
    code_hash = hash_code(code)
    candidate = await session.scalar(
        select(ClassGroup)
        .join(AcademicYear, ClassGroup.academic_year_id == AcademicYear.id)
        .join(School, AcademicYear.school_id == School.id)
        .where(
            ClassGroup.group_link_code_hash == code_hash,
            ClassGroup.is_active.is_(True),
            School.is_active.is_(True),
        )
        .with_for_update()
    )
    if candidate is None:
        raise ValueError("کد اتصال معتبر نیست یا مدرسه/کلاس غیرفعال است.")

    if candidate.group_link_code_expires_at is None:
        raise ValueError("کد اتصال فاقد تاریخ انقضا است.")
    expiry = candidate.group_link_code_expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if expiry <= now:
        raise ValueError("کد اتصال منقضی شده است.")

    school_id = await session.scalar(
        select(AcademicYear.school_id).where(AcademicYear.id == candidate.academic_year_id)
    )
    is_platform_admin = bool(actor.is_platform_admin)
    class_manager = await session.scalar(
        select(ClassMembership.id).where(
            ClassMembership.class_id == candidate.id,
            ClassMembership.user_id == actor.id,
            ClassMembership.is_active.is_(True),
            ClassMembership.role.in_(
                [Role.CLASS_MANAGER, Role.SCHOOL_ADMIN, Role.PLATFORM_ADMIN]
            ),
        )
    )
    school_manager = await session.scalar(
        select(SchoolMembership.id).where(
            SchoolMembership.school_id == school_id,
            SchoolMembership.user_id == actor.id,
            SchoolMembership.is_active.is_(True),
            SchoolMembership.role.in_([Role.SCHOOL_ADMIN, Role.PLATFORM_ADMIN]),
        )
    )
    if not is_platform_admin and class_manager is None and school_manager is None:
        raise PermissionError("فقط مدیر همین کلاس یا مدیر مدرسه می‌تواند گروه را متصل کند.")

    if candidate.group_chat_id is not None and candidate.group_chat_id != chat_id:
        raise ValueError("این کلاس از قبل به گروه دیگری متصل است.")
    existing_class_id = await session.scalar(
        select(ClassGroup.id).where(
            ClassGroup.group_chat_id == chat_id,
            ClassGroup.id != candidate.id,
        )
    )
    if existing_class_id is not None:
        raise ValueError("این گروه قبلاً به کلاس دیگری متصل شده است.")

    candidate.group_chat_id = chat_id
    candidate.group_chat_title = normalized_title
    candidate.group_link_code_hash = None
    candidate.group_link_code_expires_at = None
    await session.flush()
    return candidate
