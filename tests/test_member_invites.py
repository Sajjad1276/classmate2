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
