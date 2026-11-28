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
async def test_existing_group_gets_first_recurring_invite_immediately(world) -> None:
    class_group = world["class"]
    class_group.group_chat_id = -1001234567890
    class_group.member_join_code_token = "CURRENTJOIN42"
    class_group.member_join_code_hash = hash_code("CURRENTJOIN42")
    class_group.member_join_code_expires_at = None
    start = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)
    class_group.member_invite_reminder_next_at = start + INVITE_REMINDER_INTERVAL

    result = await schedule_group_invite_reminders(
        world["session"],
        bot_username="Classmatepro_bot",
        now=start,
    )

    assert result == {"initialized": 0, "queued": 1, "rotated": 0}
    assert class_group.member_join_code_token == "CURRENTJOIN42"
    assert class_group.member_join_code_hash == hash_code("CURRENTJOIN42")
    assert class_group.member_invite_reminder_next_at == start + INVITE_REMINDER_INTERVAL
    job = await world["session"].scalar(select(ReminderJob))
    assert job is not None
    assert job.button_url == "https://t.me/Classmatepro_bot?start=join_CURRENTJOIN42"
    assert job.scheduled_at == start

    # A queued first message must not be force-enqueued again before delivery.
    repeated = await schedule_group_invite_reminders(
        world["session"],
        bot_username="Classmatepro_bot",
        now=start + timedelta(minutes=5),
    )
    assert repeated == {"initialized": 0, "queued": 0, "rotated": 0}
    assert await world["session"].scalar(select(func.count(ReminderJob.id))) == 1


@pytest.mark.asyncio
async def test_due_group_invite_is_queued_and_reuses_the_same_link_every_48_hours(world) -> None:
    class_group = world["class"]
    class_group.group_chat_id = -1001234567891
    class_group.member_join_code_token = "REUSABLEJOIN43"
    class_group.member_join_code_hash = hash_code("REUSABLEJOIN43")
    class_group.member_join_code_expires_at = None
    start = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)
    class_group.member_invite_reminder_next_at = start

    first = await schedule_group_invite_reminders(
        world["session"],
        bot_username="Classmatepro_bot",
        now=start,
    )
    assert first == {"initialized": 0, "queued": 1, "rotated": 0}

    job = await world["session"].scalar(select(ReminderJob))
    assert job is not None
    assert job.class_id == class_group.id
    assert job.target_type == "group"
    assert job.target_id == class_group.group_chat_id
    assert job.entity_type == "class_invite"
    assert job.button_text == INVITE_BUTTON_TEXT
    assert job.message_text == INVITE_MESSAGE_TEXT
    original_url = job.button_url
    assert original_url == "https://t.me/Classmatepro_bot?start=join_REUSABLEJOIN43"
    assert class_group.member_join_code_hash == hash_code(class_group.member_join_code_token)
    assert class_group.member_join_code_expires_at is None

    second_due = class_group.member_invite_reminder_next_at
    assert second_due == start + INVITE_REMINDER_INTERVAL
    second = await schedule_group_invite_reminders(
        world["session"],
        bot_username="@Classmatepro_bot",
        now=second_due + timedelta(seconds=5),
    )
    assert second == {"initialized": 0, "queued": 1, "rotated": 0}
    jobs = list((await world["session"].scalars(select(ReminderJob).order_by(ReminderJob.id))).all())
    assert len(jobs) == 2
    assert jobs[1].button_url == original_url
    assert class_group.member_invite_reminder_next_at == second_due + timedelta(seconds=5) + INVITE_REMINDER_INTERVAL


@pytest.mark.asyncio
async def test_legacy_hash_only_group_rotates_once_and_sends_immediately(world) -> None:
    class_group = world["class"]
    class_group.group_chat_id = -1001234567892
    class_group.member_join_code_hash = hash_code("LEGACYJOIN44")
    class_group.member_join_code_token = None
    class_group.member_join_code_expires_at = None
    class_group.member_invite_reminder_next_at = None
    now = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)

    result = await schedule_group_invite_reminders(
        world["session"],
        bot_username="Classmatepro_bot",
        now=now,
    )

    assert result == {"initialized": 0, "queued": 1, "rotated": 1}
    assert class_group.member_join_code_token
    assert class_group.member_join_code_hash == hash_code(class_group.member_join_code_token)
    assert class_group.member_invite_reminder_next_at == now + INVITE_REMINDER_INTERVAL
    job = await world["session"].scalar(select(ReminderJob))
    assert job is not None
    assert job.button_url.endswith(f"join_{class_group.member_join_code_token}")
    assert job.scheduled_at == now
