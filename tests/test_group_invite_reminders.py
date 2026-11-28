from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from classmate.db.models import ReminderJob
from classmate.services.group_invites import (
    INVITE_BUTTON_TEXT,
    INVITE_MESSAGE_TEXT,
    INVITE_REMINDER_INTERVAL,
    schedule_group_invite_reminders,
)
from classmate.services.permissions import hash_code


@pytest.mark.asyncio
async def test_existing_group_gets_first_recurring_invite_after_48_hours(world) -> None:
    class_group = world["class"]
    class_group.group_chat_id = -1001234567890
    class_group.member_join_code_hash = hash_code("LEGACYJOIN42")
    class_group.member_join_code_expires_at = None
    now = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)

    result = await schedule_group_invite_reminders(
        world["session"],
        bot_username="Classmatepro_bot",
        now=now,
    )

    assert result == {"initialized": 1, "queued": 0, "rotated": 0}
    assert class_group.member_join_code_hash == hash_code("LEGACYJOIN42")
    assert class_group.member_join_code_token is None
    assert class_group.member_invite_reminder_next_at == now + timedelta(hours=48)
    assert await world["session"].scalar(select(func.count(ReminderJob.id))) == 0


@pytest.mark.asyncio
async def test_due_group_invite_is_queued_with_inline_button_and_reused_link(world) -> None:
    class_group = world["class"]
    class_group.group_chat_id = -1001234567891
    class_group.member_join_code_hash = hash_code("LEGACYJOIN43")
    class_group.member_join_code_expires_at = None
    start = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)

    await schedule_group_invite_reminders(
        world["session"],
        bot_username="Classmatepro_bot",
        now=start,
    )
    due_at = class_group.member_invite_reminder_next_at
    now = due_at + timedelta(seconds=10)

    result = await schedule_group_invite_reminders(
        world["session"],
        bot_username="@Classmatepro_bot",
        now=now,
    )

    assert result == {"initialized": 0, "queued": 1, "rotated": 1}
    job = await world["session"].scalar(select(ReminderJob))
    assert job is not None
    assert job.class_id == class_group.id
    assert job.target_type == "group"
    assert job.target_id == class_group.group_chat_id
    assert job.entity_type == "class_invite"
    assert job.button_text == INVITE_BUTTON_TEXT
    assert job.message_text == INVITE_MESSAGE_TEXT
    assert job.button_url == f"https://t.me/Classmatepro_bot?start=join_{class_group.member_join_code_token}"
    assert class_group.member_join_code_hash == hash_code(class_group.member_join_code_token)
    assert class_group.member_join_code_expires_at is None
    assert class_group.member_invite_reminder_next_at == now + INVITE_REMINDER_INTERVAL

    # The scheduler must reuse the same deep link on the next 48-hour cycle.
    original_url = job.button_url
    second_due = class_group.member_invite_reminder_next_at
    next_result = await schedule_group_invite_reminders(
        world["session"],
        bot_username="Classmatepro_bot",
        now=second_due + timedelta(seconds=5),
    )
    assert next_result == {"initialized": 0, "queued": 1, "rotated": 0}
    jobs = list((await world["session"].scalars(select(ReminderJob).order_by(ReminderJob.id))).all())
    assert len(jobs) == 2
    assert jobs[1].button_url == original_url

