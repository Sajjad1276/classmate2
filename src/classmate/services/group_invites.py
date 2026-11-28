"""Recurring, class-scoped Telegram invite links for linked class groups."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.core.enums import ReminderStatus
from classmate.db.models import AcademicYear, ClassGroup, ReminderJob, School
from classmate.services.permissions import hash_code, make_code

INVITE_REMINDER_INTERVAL = timedelta(hours=48)
INVITE_BUTTON_TEXT = "🎓 عضویت در کلاس"
INVITE_MESSAGE_TEXT = (
    "🎓 <b>عضویت در کلاس در ClassMate</b>\n\n"
    "اگر هنوز عضو این کلاس نشده‌اید، از دکمهٔ زیر وارد ربات شوید "
    "و عضویت خود را ثبت کنید.\n"
    "اگر از قبل عضو هستید، نیازی به انجام دوبارهٔ این کار نیست."
)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def schedule_group_invite_reminders(
    session: AsyncSession,
    *,
    bot_username: str,
    now: datetime | None = None,
) -> dict[str, int]:
    """Schedule at most one invite reminder per linked class every 48 hours.

    Existing classes without a recoverable raw deep-link token get a 48-hour
    transition window. At the first reminder, a new shared token is created
    and persisted alongside its hash. Newly linked classes persist their token
    at connection time and reuse the exact same URL for every subsequent post.
    """
    username = (bot_username or "").strip().removeprefix("@")
    if not username:
        return {"initialized": 0, "queued": 0, "rotated": 0}

    current = _as_utc(now or datetime.now(timezone.utc))
    classes = (
        await session.scalars(
            select(ClassGroup)
            .join(AcademicYear, ClassGroup.academic_year_id == AcademicYear.id)
            .join(School, AcademicYear.school_id == School.id)
            .where(
                ClassGroup.is_active.is_(True),
                School.is_active.is_(True),
                ClassGroup.group_chat_id.is_not(None),
                or_(
                    ClassGroup.member_invite_reminder_next_at.is_(None),
                    ClassGroup.member_invite_reminder_next_at <= current,
                ),
            )
            .order_by(ClassGroup.id)
            .with_for_update(skip_locked=True)
        )
    ).all()

    initialized = 0
    rotated = 0
    queued = 0
    for class_group in classes:
        next_at = class_group.member_invite_reminder_next_at
        if next_at is None:
            class_group.member_invite_reminder_next_at = current + INVITE_REMINDER_INTERVAL
            initialized += 1
            continue

        due_at = _as_utc(next_at)
        if due_at > current:
            continue

        token = class_group.member_join_code_token
        token_is_valid = bool(
            token
            and class_group.member_join_code_hash == hash_code(token)
            and (
                class_group.member_join_code_expires_at is None
                or _as_utc(class_group.member_join_code_expires_at) > current
            )
        )
        if not token_is_valid:
            token = make_code(12)
            class_group.member_join_code_token = token
            class_group.member_join_code_hash = hash_code(token)
            class_group.member_join_code_expires_at = None
            rotated += 1

        invite_url = f"https://t.me/{username}?start=join_{token}"
        chat_id = int(class_group.group_chat_id)
        slot = int(due_at.timestamp())
        dedupe_key = f"class-invite:{class_group.id}:{slot}:{chat_id}"
        job_values = {
            "class_id": class_group.id,
            "entity_type": "class_invite",
            "entity_id": class_group.id,
            "target_type": "group",
            "target_id": chat_id,
            "message_text": INVITE_MESSAGE_TEXT,
            "button_text": INVITE_BUTTON_TEXT,
            "button_url": invite_url,
            "scheduled_at": current,
            "status": ReminderStatus.PENDING,
            "attempts": 0,
            "dedupe_key": dedupe_key,
        }

        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            result = await session.execute(
                pg_insert(ReminderJob)
                .values(**job_values)
                .on_conflict_do_nothing(index_elements=[ReminderJob.dedupe_key])
                .returning(ReminderJob.id)
            )
        elif dialect == "sqlite":
            result = await session.execute(
                sqlite_insert(ReminderJob)
                .values(**job_values)
                .on_conflict_do_nothing(index_elements=[ReminderJob.dedupe_key])
                .returning(ReminderJob.id)
            )
        else:
            raise RuntimeError(f"Unsupported database dialect: {dialect}")

        inserted_ids = result.scalars().all()
        queued += len(inserted_ids)
        # Do not enqueue missed intervals in a burst after a worker outage.
        # Start the next full 48-hour interval from this scheduling pass.
        class_group.member_invite_reminder_next_at = current + INVITE_REMINDER_INTERVAL

    await session.flush()
    return {"initialized": initialized, "queued": queued, "rotated": rotated}
