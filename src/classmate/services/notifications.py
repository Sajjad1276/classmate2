"""Reminder job creation."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from classmate.core.enums import EntityType, ReminderStatus
from classmate.db.models import ClassGroup, Exam, Homework, NotificationPreference, ReminderJob, User
from sqlalchemy import select


async def schedule_approved_record_reminders(
    session: AsyncSession,
    class_group: ClassGroup,
    record: Homework | Exam,
    entity_type: str,
    timezone_name: str,
) -> None:
    zone = ZoneInfo(timezone_name)
    now = datetime.now(zone)

    jobs: list[tuple[datetime, str]] = []

    if entity_type == EntityType.EXAM.value and isinstance(record, Exam):
        t = record.scheduled_at.astimezone(zone)
        jobs.append((t - timedelta(hours=24), f"یادآوری امتحان (۲۴ ساعت مانده): {record.title}"))
        jobs.append((t - timedelta(hours=1), f"یادآوری امتحان (۱ ساعت مانده): {record.title}"))
    elif entity_type == EntityType.HOMEWORK.value and isinstance(record, Homework):
        if record.due_on:
            due_evening = datetime.combine(record.due_on, datetime.min.time(), tzinfo=zone) - timedelta(
                hours=6
            )  # ~18:00 previous day-ish; simplified
            # Prefer day-before 18:00
            day_before = record.due_on - timedelta(days=1)
            remind_at = datetime(day_before.year, day_before.month, day_before.day, 18, 0, tzinfo=zone)
            if remind_at > now:
                jobs.append((remind_at, f"یادآوری تکلیف: {record.title}"))

    for scheduled_at, text in jobs:
        if scheduled_at <= now:
            continue

        # Group reminder
        if class_group.group_chat_id:
            dedupe = f"{entity_type}:{record.id}:group:{class_group.group_chat_id}:{int(scheduled_at.timestamp())}"
            session.add(
                ReminderJob(
                    class_id=class_group.id,
                    entity_type=entity_type,
                    entity_id=record.id,
                    target_type="group",
                    target_id=class_group.group_chat_id,
                    message_text=text,
                    scheduled_at=scheduled_at,
                    status=ReminderStatus.PENDING,
                    dedupe_key=dedupe,
                )
            )

        # Private reminders for members who started the bot and have prefs enabled
        members = (
            await session.execute(
                select(User)
                .join(NotificationPreference, NotificationPreference.user_id == User.id)
                .where(
                    NotificationPreference.class_id == class_group.id,
                    User.private_chat_started.is_(True),
                    User.is_active.is_(True),
                )
            )
        ).scalars().all()

        # Also include active class members without explicit preference (default on)
        from classmate.db.models import ClassMembership

        all_members = (
            await session.execute(
                select(User)
                .join(ClassMembership, ClassMembership.user_id == User.id)
                .where(
                    ClassMembership.class_id == class_group.id,
                    ClassMembership.is_active.is_(True),
                    User.private_chat_started.is_(True),
                    User.is_active.is_(True),
                )
            )
        ).scalars().all()

        seen: set[int] = set()
        for user in all_members:
            if user.telegram_id in seen:
                continue
            seen.add(user.telegram_id)

            pref = await session.scalar(
                select(NotificationPreference).where(
                    NotificationPreference.user_id == user.id,
                    NotificationPreference.class_id == class_group.id,
                )
            )
            if pref is not None:
                if entity_type == EntityType.HOMEWORK.value and not pref.homework_enabled:
                    continue
                if entity_type == EntityType.EXAM.value and not pref.exams_enabled:
                    continue

            dedupe = f"{entity_type}:{record.id}:private:{user.telegram_id}:{int(scheduled_at.timestamp())}"
            session.add(
                ReminderJob(
                    class_id=class_group.id,
                    entity_type=entity_type,
                    entity_id=record.id,
                    target_type="private",
                    target_id=user.telegram_id,
                    message_text=text,
                    scheduled_at=scheduled_at,
                    status=ReminderStatus.PENDING,
                    dedupe_key=dedupe,
                )
            )

    await session.flush()
