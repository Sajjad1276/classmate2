"""Learner access and invitation-code helpers."""

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
    ClassMemberInviteCode,
    School,
    SchoolMembership,
    User,
)


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


_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def make_code(length: int = 8) -> str:
    if not 4 <= length <= 64:
        raise ValueError("طول کد باید بین ۴ تا ۶۴ نویسه باشد.")
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(length))


async def is_class_manager(session: AsyncSession, user: User, class_id: int) -> bool:
    if not user.is_active or await get_class_with_school(session, class_id) is None:
        return False
    if user.is_platform_admin:
        return True
    membership = await session.scalar(
        select(ClassMembership.id).where(
            ClassMembership.class_id == class_id,
            ClassMembership.user_id == user.id,
            ClassMembership.is_active.is_(True),
            ClassMembership.role.in_([Role.CLASS_MANAGER, Role.SCHOOL_ADMIN, Role.PLATFORM_ADMIN]),
        )
    )
    if membership is not None:
        return True
    school_id = await session.scalar(
        select(AcademicYear.school_id)
        .join(ClassGroup, ClassGroup.academic_year_id == AcademicYear.id)
        .where(ClassGroup.id == class_id)
    )
    if school_id is None:
        return False
    return (
        await session.scalar(
            select(SchoolMembership.id).where(
                SchoolMembership.school_id == school_id,
                SchoolMembership.user_id == user.id,
                SchoolMembership.is_active.is_(True),
                SchoolMembership.role.in_([Role.SCHOOL_ADMIN, Role.PLATFORM_ADMIN]),
            )
        )
        is not None
    )


async def can_approve(session: AsyncSession, user: User, class_id: int) -> bool:
    if not user.is_active or await get_class_with_school(session, class_id) is None:
        return False
    if user.is_platform_admin:
        return True
    membership = await session.scalar(
        select(ClassMembership.id).where(
            ClassMembership.class_id == class_id,
            ClassMembership.user_id == user.id,
            ClassMembership.is_active.is_(True),
            ClassMembership.role.in_(
                [
                    Role.CLASS_MANAGER,
                    Role.TRUSTED_APPROVER,
                    Role.SCHOOL_ADMIN,
                    Role.PLATFORM_ADMIN,
                ]
            ),
        )
    )
    if membership is not None:
        return True
    school_id = await session.scalar(
        select(AcademicYear.school_id)
        .join(ClassGroup, ClassGroup.academic_year_id == AcademicYear.id)
        .where(ClassGroup.id == class_id)
    )
    if school_id is None:
        return False
    return (
        await session.scalar(
            select(SchoolMembership.id).where(
                SchoolMembership.school_id == school_id,
                SchoolMembership.user_id == user.id,
                SchoolMembership.is_active.is_(True),
                SchoolMembership.role.in_([Role.SCHOOL_ADMIN, Role.PLATFORM_ADMIN]),
            )
        )
        is not None
    )




async def consume_class_member_join_code(
    session: AsyncSession,
    *,
    code: str,
    user: User,
) -> ClassGroup:
    """Join a class using its reusable ClassMate-native member_join_code.

    A class code is shared by all learners in that class. It remains valid until
    a manager rotates it; a nullable expiry therefore means no scheduled expiry.
    Previously-issued per-user codes are still accepted as a compatibility path.
    """
    normalized_code = code.strip().upper()
    if not normalized_code or len(normalized_code) > 64:
        raise ValueError("کد ورود کلاس معتبر نیست.")
    if not user.is_active:
        raise PermissionError("حساب کاربری شما غیرفعال است.")

    code_hash = hash_code(normalized_code)
    class_group = await session.scalar(
        select(ClassGroup)
        .join(AcademicYear, ClassGroup.academic_year_id == AcademicYear.id)
        .join(School, AcademicYear.school_id == School.id)
        .where(
            ClassGroup.member_join_code_hash == code_hash,
            ClassGroup.is_active.is_(True),
            School.is_active.is_(True),
        )
        .with_for_update()
    )

    if class_group is None:
        # Do not strand students who received the older, one-user invitations
        # before class-level codes became the default.
        return await consume_class_member_invite_code(
            session,
            code=normalized_code,
            user=user,
        )

    expires_at = class_group.member_join_code_expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            raise ValueError("کد ورود این کلاس منقضی شده است؛ از مدیر کلاس کد تازه بگیرید.")

    membership = await session.scalar(
        select(ClassMembership)
        .where(
            ClassMembership.class_id == class_group.id,
            ClassMembership.user_id == user.id,
        )
        .with_for_update()
    )
    if membership is not None:
        if membership.role != Role.STUDENT:
            raise PermissionError(
                "این حساب در کلاس نقش مدیریتی دارد؛ برای تغییر نقش با مدیر سامانه تماس بگیرید."
            )
        if membership.is_active:
            raise ValueError(f"شما از قبل عضو فعال کلاس «{class_group.name}» هستید.")
        membership.is_active = True
    else:
        session.add(
            ClassMembership(
                class_id=class_group.id,
                user_id=user.id,
                role=Role.STUDENT,
                is_active=True,
            )
        )

    await session.flush()
    return class_group


async def consume_class_member_invite_code(
    session: AsyncSession,
    *,
    code: str,
    user: User,
) -> ClassGroup:
    normalized_code = code.strip().upper()
    if not normalized_code or len(normalized_code) > 64:
        raise ValueError("کد عضویت معتبر نیست.")
    if not user.is_active:
        raise PermissionError("حساب کاربری شما غیرفعال است.")

    invite = await session.scalar(
        select(ClassMemberInviteCode)
        .where(ClassMemberInviteCode.code_hash == hash_code(normalized_code))
        .with_for_update()
    )
    if invite is None:
        raise ValueError("کد عضویت اختصاصی معتبر نیست.")
    if invite.user_id != user.id:
        raise PermissionError("این کد برای حساب تلگرام دیگری صادر شده است.")
    if invite.used_at is not None:
        raise ValueError("این کد قبلاً استفاده شده است.")
    expires_at = invite.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if expires_at <= now:
        raise ValueError("مهلت استفاده از این کد به پایان رسیده است.")

    class_context = await get_class_with_school(session, invite.class_id)
    if class_context is None:
        raise ValueError("کلاس یا مدرسه غیرفعال است.")
    class_group = class_context[0]
    membership = await session.scalar(
        select(ClassMembership)
        .where(
            ClassMembership.class_id == invite.class_id,
            ClassMembership.user_id == user.id,
        )
        .with_for_update()
    )
    if membership is None or membership.role != Role.STUDENT:
        raise PermissionError("عضویت دانش‌آموز برای این حساب در این کلاس ثبت نشده است.")

    await activate_student_membership(session, invite.class_id, user.id)
    invite.used_at = now
    await session.flush()
    return class_group
