from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from classmate.core.enums import Role
from classmate.db.models import ClassMemberInviteCode, ClassMembership
from classmate.services.permissions import consume_class_member_invite_code, hash_code


@pytest.mark.asyncio
async def test_student_invite_is_bound_to_user_and_class_and_consumed_once(world):
    session = world["session"]
    student = world["student"]
    membership = await session.scalar(
        select(ClassMembership).where(
            ClassMembership.class_id == world["class"].id,
            ClassMembership.user_id == student.id,
        )
    )
    membership.is_active = False
    code = "STUDENTCLASSINVITE001"
    invitation = ClassMemberInviteCode(
        class_id=world["class"].id,
        user_id=student.id,
        code_hash=hash_code(code),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    session.add(invitation)
    await session.flush()

    joined_class = await consume_class_member_invite_code(
        session,
        code=code,
        user=student,
    )

    assert joined_class.id == world["class"].id
    assert membership.is_active is True
    assert membership.role == Role.STUDENT
    assert invitation.used_at is not None

    with pytest.raises(ValueError, match="قبلاً استفاده"):
        await consume_class_member_invite_code(session, code=code, user=student)


@pytest.mark.asyncio
async def test_student_invite_rejects_a_different_telegram_user(world):
    session = world["session"]
    code = "STUDENTCLASSINVITE002"
    invitation = ClassMemberInviteCode(
        class_id=world["class"].id,
        user_id=world["student"].id,
        code_hash=hash_code(code),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    session.add(invitation)
    await session.flush()

    with pytest.raises(PermissionError, match="حساب تلگرام دیگری"):
        await consume_class_member_invite_code(
            session,
            code=code,
            user=world["manager"],
        )

    assert invitation.used_at is None


@pytest.mark.asyncio
async def test_student_invite_rejects_expired_code(world):
    session = world["session"]
    code = "STUDENTCLASSINVITE003"
    invitation = ClassMemberInviteCode(
        class_id=world["class"].id,
        user_id=world["student"].id,
        code_hash=hash_code(code),
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    session.add(invitation)
    await session.flush()

    with pytest.raises(ValueError, match="مهلت"):
        await consume_class_member_invite_code(
            session,
            code=code,
            user=world["student"],
        )

    assert invitation.used_at is None



@pytest.mark.asyncio
async def test_shared_class_code_creates_student_membership_for_new_user(world):
    from classmate.db.models import User
    from classmate.services.permissions import consume_class_member_join_code

    session = world["session"]
    code = "CLASSJOIN8X2M"
    world["class"].member_join_code_hash = hash_code(code)
    world["class"].member_join_code_expires_at = None
    new_student = User(
        telegram_id=909001,
        username="new_student",
        full_name="دانش‌آموز جدید",
        private_chat_started=True,
    )
    session.add(new_student)
    await session.flush()

    joined_class = await consume_class_member_join_code(
        session,
        code=code,
        user=new_student,
    )

    membership = await session.scalar(
        select(ClassMembership).where(
            ClassMembership.class_id == world["class"].id,
            ClassMembership.user_id == new_student.id,
        )
    )
    assert joined_class.id == world["class"].id
    assert membership is not None
    assert membership.role == Role.STUDENT
    assert membership.is_active is True


@pytest.mark.asyncio
async def test_shared_class_code_reactivates_suspended_student_but_not_manager(world):
    from classmate.services.permissions import consume_class_member_join_code

    session = world["session"]
    code = "CLASSJOIN8X2N"
    world["class"].member_join_code_hash = hash_code(code)

    student_membership = await session.scalar(
        select(ClassMembership).where(
            ClassMembership.class_id == world["class"].id,
            ClassMembership.user_id == world["student"].id,
        )
    )
    student_membership.is_active = False
    await consume_class_member_join_code(session, code=code, user=world["student"])
    assert student_membership.is_active is True
    assert student_membership.role == Role.STUDENT

    manager_membership = await session.scalar(
        select(ClassMembership).where(
            ClassMembership.class_id == world["class"].id,
            ClassMembership.user_id == world["manager"].id,
        )
    )
    manager_membership.is_active = False
    with pytest.raises(PermissionError, match="نقش مدیریتی"):
        await consume_class_member_join_code(session, code=code, user=world["manager"])
    assert manager_membership.is_active is False
    assert manager_membership.role == Role.CLASS_MANAGER


@pytest.mark.asyncio
async def test_shared_class_code_rejects_expired_code_and_inactive_class(world):
    from classmate.services.permissions import consume_class_member_join_code

    session = world["session"]
    code = "CLASSJOIN8X2P"
    world["class"].member_join_code_hash = hash_code(code)
    world["class"].member_join_code_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    with pytest.raises(ValueError, match="منقضی"):
        await consume_class_member_join_code(session, code=code, user=world["student"])

    world["class"].member_join_code_expires_at = None
    world["class"].is_active = False
    with pytest.raises(ValueError, match="کد عضویت اختصاصی معتبر نیست"):
        await consume_class_member_join_code(session, code=code, user=world["student"])


@pytest.mark.asyncio
async def test_shared_class_code_is_reusable_by_multiple_students(world):
    from classmate.db.models import User
    from classmate.services.permissions import consume_class_member_join_code

    session = world["session"]
    code = "CLASSJOIN8X2Q"
    world["class"].member_join_code_hash = hash_code(code)
    one = User(telegram_id=909002, full_name="دانش‌آموز یک", private_chat_started=True)
    two = User(telegram_id=909003, full_name="دانش‌آموز دو", private_chat_started=True)
    session.add_all([one, two])
    await session.flush()

    await consume_class_member_join_code(session, code=code, user=one)
    await consume_class_member_join_code(session, code=code, user=two)

    assert await session.scalar(
        select(ClassMembership.id).where(
            ClassMembership.class_id == world["class"].id,
            ClassMembership.user_id == one.id,
            ClassMembership.is_active.is_(True),
        )
    )
    assert await session.scalar(
        select(ClassMembership.id).where(
            ClassMembership.class_id == world["class"].id,
            ClassMembership.user_id == two.id,
            ClassMembership.is_active.is_(True),
        )
    )


@pytest.mark.asyncio
async def test_native_shared_code_path_keeps_old_personal_codes_compatible(world):
    from classmate.services.permissions import consume_class_member_join_code

    session = world["session"]
    code = "LEGACYCODE87654321"
    invitation = ClassMemberInviteCode(
        class_id=world["class"].id,
        user_id=world["student"].id,
        code_hash=hash_code(code),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    session.add(invitation)
    await session.flush()

    joined_class = await consume_class_member_join_code(
        session,
        code=code,
        user=world["student"],
    )

    assert joined_class.id == world["class"].id
    assert invitation.used_at is not None
