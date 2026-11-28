"""Idempotent Class 104 dated plan and eight-hour reminder scheduling."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import jdatetime
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.core.enums import EntityType, ReminderStatus
from classmate.db.models import (
    AcademicYear,
    Announcement,
    ClassGroup,
    ClassMembership,
    ReminderJob,
    School,
    User,
)

logger = logging.getLogger(__name__)
ANNOUNCEMENT_TITLE = "◈ برنامهٔ پیش‌رو و یادآوری‌ها — کلاس دهم ۱۰۴"
REMINDER_INTERVAL = timedelta(hours=8)
INTERVAL_SECONDS = int(REMINDER_INTERVAL.total_seconds())


def _ascii_digits(value: str) -> str:
    table = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    return value.translate(table)


def _jalali_day(day: int) -> date:
    return jdatetime.date(1405, 9, day).togregorian()


def _events() -> list[tuple[date, str, list[str]]]:
    """Class 104 (Math 2) timetable verified against the 1405-1406 school PDF."""
    sunday = [
        "• دفاعی — تدریس",
        "• فارسی — تدریس",
        "• جغرافیا — مطالعهٔ درس‌های ۲ و ۳",
    ]
    monday = [
        "• فیزیک — تدریس",
        "• ریاضی — تدریس",
        "• رسانه — تدریس",
        "• زبان — تدریس",
    ]
    tuesday = [
        "• دینی — تدریس",
        "• شیمی — تدریس (۲ زنگ)",
        "• عربی — تدریس",
    ]
    wednesday = [
        "• آزمایشگاه — تدریس",
        "• هندسه — تدریس",
        "• نگارش — تدریس",
    ]
    return [
        (
            _jalali_day(8),
            "یکشنبه | ۸ آذر ۱۴۰۵",
            sunday,
        ),
        (
            _jalali_day(9),
            "دوشنبه | ۹ آذر ۱۴۰۵",
            [
                *monday,
                "↫ امتحان زبان انگلیسی",
                "↫ امتحان دینی",
            ],
        ),
        (
            _jalali_day(10),
            "سه‌شنبه | ۱۰ آذر ۱۴۰۵",
            [
                *tuesday,
                "• تکلیف یا امتحان جداگانه‌ای برای این روز ثبت نشده است.",
            ],
        ),
        (
            _jalali_day(11),
            "چهارشنبه | ۱۱ آذر ۱۴۰۵",
            [
                *wednesday,
                "↫ امتحان هندسه — مرور جزوه و مطالعهٔ کتاب تا صفحهٔ ۱۶",
                "↫ نگارش — تکمیل ۳ انشا",
            ],
        ),
        (
            _jalali_day(24),
            "سه‌شنبه | ۲۴ آذر ۱۴۰۵",
            [
                *tuesday,
                "↫ امتحان عربی",
            ],
        ),
    ]


def build_class104_plan_body(from_date: date | None = None) -> str:
    """Show only dates after the current local day; empty days are omitted."""
    if from_date is None:
        from_date = datetime.now(ZoneInfo("Asia/Tehran")).date()
    lines = ["◈ برنامهٔ پیش‌رو — کلاس دهم ۱۰۴"]
    included = 0
    for event_date, heading, items in _events():
        # Do not keep today's plan in a reminder once that date has arrived.
        if event_date <= from_date:
            continue
        lines.extend(["", heading, *items])
        included += 1
    if not included:
        return ""
    lines.extend(["", "ᯓ ClassMate | کلاس ۱۰۴"])
    return "\n".join(lines)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def next_class104_reminder_at(
    published_at: datetime,
    last_activity_at: datetime | None = None,
) -> datetime:
    """Return one next reminder slot, anchored to the last delivery/attempt."""
    if last_activity_at is None:
        return _utc(published_at)
    return _utc(last_activity_at) + REMINDER_INTERVAL



async def _find_class104(session: AsyncSession) -> tuple[ClassGroup, School] | None:
    rows = (
        await session.execute(
            select(ClassGroup, School, AcademicYear)
            .join(AcademicYear, ClassGroup.academic_year_id == AcademicYear.id)
            .join(School, AcademicYear.school_id == School.id)
            .where(ClassGroup.is_active.is_(True), School.is_active.is_(True))
        )
    ).all()

    matches: list[tuple[ClassGroup, School, AcademicYear]] = []
    for class_group, school, academic_year in rows:
        name = _ascii_digits(
            f"{class_group.name} {class_group.grade} {class_group.section or ''}"
        ).lower()
        grade = _ascii_digits(str(class_group.grade)).strip().lower()
        section = _ascii_digits(str(class_group.section or "")).strip()
        grade_ten = grade in {"10", "۱۰"} or "دهم" in name or "grade 10" in name
        class_104 = section == "104" or "104" in name
        if grade_ten and class_104:
            matches.append((class_group, school, academic_year))

    if len(matches) > 1:
        current = [item for item in matches if item[2].is_current]
        if len(current) == 1:
            matches = current
    if len(matches) != 1:
        if not matches:
            logger.warning("No active Class 104 matched; plan seeding skipped")
        else:
            logger.error(
                "Multiple active Class 104 records matched; refusing ambiguous seed: %s",
                [item[0].id for item in matches],
            )
        return None
    class_group, school, _ = matches[0]
    return class_group, school


async def _insert_jobs(session: AsyncSession, values: list[dict[str, object]]) -> int:
    if not values:
        return 0
    dialect = session.get_bind().dialect.name
    inserted = 0
    for offset in range(0, len(values), 500):
        batch = values[offset : offset + 500]
        if dialect == "postgresql":
            statement = (
                pg_insert(ReminderJob)
                .values(batch)
                .on_conflict_do_nothing(index_elements=[ReminderJob.dedupe_key])
                .returning(ReminderJob.id)
            )
        elif dialect == "sqlite":
            statement = (
                sqlite_insert(ReminderJob)
                .values(batch)
                .on_conflict_do_nothing(index_elements=[ReminderJob.dedupe_key])
                .returning(ReminderJob.id)
            )
        else:
            raise RuntimeError(f"Unsupported database dialect: {dialect}")
        result = await session.execute(statement)
        inserted += len(result.scalars().all())
    return inserted

async def seed_class104_plan(session: AsyncSession) -> dict[str, int] | None:
    """Refresh the plan and enqueue at most one reminder cycle per 8-hour interval."""
    target = await _find_class104(session)
    if target is None:
        return None
    class_group, school = target

    members = (
        await session.scalars(
            select(User)
            .join(ClassMembership, ClassMembership.user_id == User.id)
            .where(
                ClassMembership.class_id == class_group.id,
                ClassMembership.is_active.is_(True),
                User.is_active.is_(True),
            )
            .order_by(User.id)
        )
    ).all()
    if not members:
        logger.warning("Class 104 has no active membership; plan seeding skipped")
        return None

    try:
        zone = ZoneInfo(school.timezone or "Asia/Tehran")
    except Exception:
        logger.exception("Invalid school timezone %r; using Asia/Tehran", school.timezone)
        zone = ZoneInfo("Asia/Tehran")

    now = datetime.now(timezone.utc)
    today = now.astimezone(zone).date()
    expiry = datetime.combine(
        _jalali_day(24),
        time(23, 59, 59),
        tzinfo=zone,
    ).astimezone(timezone.utc)
    full_body = build_class104_plan_body(today)

    announcements = (
        await session.scalars(
            select(Announcement)
            .where(
                Announcement.class_id == class_group.id,
                Announcement.title == ANNOUNCEMENT_TITLE,
            )
            .order_by(Announcement.id.desc())
        )
    ).all()
    announcement = announcements[0] if announcements else None

    # Earlier parallel worker starts could create more than one plan announcement.
    # Keep the newest record and stop every unsent reminder attached to older copies.
    duplicate_ids = [row.id for row in announcements[1:]]
    if duplicate_ids:
        await session.execute(
            update(ReminderJob)
            .where(
                ReminderJob.entity_type == EntityType.ANNOUNCEMENT.value,
                ReminderJob.entity_id.in_(duplicate_ids),
                ReminderJob.status.in_(
                    [
                        ReminderStatus.PENDING,
                        ReminderStatus.RETRY,
                        ReminderStatus.PROCESSING,
                    ]
                ),
            )
            .values(status=ReminderStatus.CANCELLED, locked_at=None)
        )
        for duplicate in announcements[1:]:
            duplicate.is_active = False

    if announcement is None:
        announcement = Announcement(
            class_id=class_group.id,
            title=ANNOUNCEMENT_TITLE,
            body=full_body,
            created_by=members[0].id,
            published_at=now,
            expires_at=expiry,
            is_active=True,
        )
        session.add(announcement)
        await session.flush()
    else:
        old_expiry = _utc(announcement.expires_at) if announcement.expires_at else None
        content_changed = (
            announcement.body != full_body
            or old_expiry != expiry
            or not announcement.is_active
        )
        if content_changed:
            # Refresh text without resetting the 8-hour delivery clock.
            announcement.body = full_body
            announcement.expires_at = expiry
            announcement.is_active = True
            await session.flush()

    if expiry <= now:
        announcement.is_active = False
        await session.execute(
            update(ReminderJob)
            .where(
                ReminderJob.entity_type == EntityType.ANNOUNCEMENT.value,
                ReminderJob.entity_id == announcement.id,
                ReminderJob.status.in_(
                    [
                        ReminderStatus.PENDING,
                        ReminderStatus.RETRY,
                        ReminderStatus.PROCESSING,
                    ]
                ),
            )
            .values(status=ReminderStatus.CANCELLED, locked_at=None)
        )
        return {
            "class_id": class_group.id,
            "announcement_id": announcement.id,
            "reminder_jobs": 0,
            "candidate_reminder_jobs": 0,
            "group_targets": 0,
            "private_targets": 0,
        }

    if not full_body:
        await session.execute(
            update(ReminderJob)
            .where(
                ReminderJob.entity_type == EntityType.ANNOUNCEMENT.value,
                ReminderJob.entity_id == announcement.id,
                ReminderJob.status.in_(
                    [
                        ReminderStatus.PENDING,
                        ReminderStatus.RETRY,
                        ReminderStatus.PROCESSING,
                    ]
                ),
            )
            .values(status=ReminderStatus.CANCELLED, locked_at=None)
        )
        return {
            "class_id": class_group.id,
            "announcement_id": announcement.id,
            "reminder_jobs": 0,
            "candidate_reminder_jobs": 0,
            "group_targets": 0,
            "private_targets": 0,
        }

    targets: list[tuple[str, int, int | None]] = []
    group_targets = 0
    private_targets = 0
    if class_group.group_chat_id:
        targets.append(("group", int(class_group.group_chat_id), None))
        group_targets = 1
    for member in members:
        if member.private_chat_started:
            targets.append(("private", int(member.telegram_id), member.id))
            private_targets += 1

    if not targets:
        logger.warning(
            "Class 104 plan exists but has no delivery target: group_chat_linked=%s private_started_members=%s",
            bool(class_group.group_chat_id),
            private_targets,
        )
        return {
            "class_id": class_group.id,
            "announcement_id": announcement.id,
            "reminder_jobs": 0,
            "candidate_reminder_jobs": 0,
            "group_targets": group_targets,
            "private_targets": private_targets,
        }

    # Cancel the old bulk-prequeued backlog. New jobs use a versioned key and
    # only represent the current 8-hour slot.
    key_prefix = f"classplan-v2:{announcement.id}:"
    await session.execute(
        update(ReminderJob)
        .where(
            ReminderJob.entity_type == EntityType.ANNOUNCEMENT.value,
            ReminderJob.entity_id == announcement.id,
            ReminderJob.status.in_(
                [
                    ReminderStatus.PENDING,
                    ReminderStatus.RETRY,
                    ReminderStatus.PROCESSING,
                ]
            ),
            ~ReminderJob.dedupe_key.startswith(key_prefix),
        )
        .values(status=ReminderStatus.CANCELLED, locked_at=None)
    )

    current_jobs = (
        await session.scalars(
            select(ReminderJob).where(
                ReminderJob.entity_type == EntityType.ANNOUNCEMENT.value,
                ReminderJob.entity_id == announcement.id,
                ReminderJob.dedupe_key.startswith(key_prefix),
                ReminderJob.status.in_(
                    [
                        ReminderStatus.PENDING,
                        ReminderStatus.RETRY,
                        ReminderStatus.PROCESSING,
                    ]
                ),
            )
        )
    ).all()
    message_body = build_class104_plan_body(today)
    message = f"⌯ یادآوری برنامهٔ کلاس ۱۰۴\n\n{message_body}"

    # If this slot is already in flight, finish it; do not create another one.
    if current_jobs:
        for job in current_jobs:
            if job.status in [ReminderStatus.PENDING, ReminderStatus.RETRY]:
                job.message_text = message
        return {
            "class_id": class_group.id,
            "announcement_id": announcement.id,
            "reminder_jobs": 0,
            "candidate_reminder_jobs": 0,
            "group_targets": group_targets,
            "private_targets": private_targets,
        }

    last_activity = await session.scalar(
        select(
            func.max(
                func.coalesce(ReminderJob.sent_at, ReminderJob.scheduled_at)
            )
        ).where(
            ReminderJob.entity_type == EntityType.ANNOUNCEMENT.value,
            ReminderJob.entity_id == announcement.id,
            ReminderJob.status.in_([ReminderStatus.SENT, ReminderStatus.FAILED]),
        )
    )
    next_due = next_class104_reminder_at(announcement.published_at, last_activity)
    if now < next_due:
        return {
            "class_id": class_group.id,
            "announcement_id": announcement.id,
            "reminder_jobs": 0,
            "candidate_reminder_jobs": 0,
            "group_targets": group_targets,
            "private_targets": private_targets,
        }

    slot_ts = int(next_due.timestamp())
    message_body = build_class104_plan_body(today)
    if not message_body:
        return {
            "class_id": class_group.id,
            "announcement_id": announcement.id,
            "reminder_jobs": 0,
            "candidate_reminder_jobs": 0,
            "group_targets": group_targets,
            "private_targets": private_targets,
        }
    message = f"⌯ یادآوری برنامهٔ کلاس ۱۰۴\n\n{message_body}"

    values: list[dict[str, object]] = []
    for target_type, target_id, _recipient_id in targets:
        values.append(
            {
                "class_id": class_group.id,
                "entity_type": EntityType.ANNOUNCEMENT.value,
                "entity_id": announcement.id,
                "target_type": target_type,
                "target_id": target_id,
                "message_text": message,
                "scheduled_at": now,
                "status": ReminderStatus.PENDING,
                "attempts": 0,
                "dedupe_key": (
                    f"{key_prefix}{slot_ts}:{target_type}:{target_id}"
                ),
            }
        )

    inserted = await _insert_jobs(session, values)
    return {
        "class_id": class_group.id,
        "announcement_id": announcement.id,
        "reminder_jobs": inserted,
        "candidate_reminder_jobs": len(values),
        "group_targets": group_targets,
        "private_targets": private_targets,
    }
