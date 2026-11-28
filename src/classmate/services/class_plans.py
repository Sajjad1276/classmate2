"""Idempotent Class 104 dated plan and eight-hour reminder scheduling."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import jdatetime
from sqlalchemy import select, update
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
    return [
        (
            _jalali_day(8),
            "یکشنبه | ۸ آذر ۱۴۰۵",
            [
                "• فارسی — تدریس",
                "• جغرافیا — مطالعهٔ درس‌های ۲ و ۳",
                "• دفاعی — تدریس",
            ],
        ),
        (
            _jalali_day(9),
            "دوشنبه | ۹ آذر ۱۴۰۵",
            [
                "• فیزیک — تدریس",
                "↫ امتحان زبان انگلیسی",
                "• عربی — تدریس",
                "↫ امتحان دینی",
            ],
        ),
        (
            _jalali_day(10),
            "سه‌شنبه | ۱۰ آذر ۱۴۰۵",
            ["• برای این روز تکلیف یا امتحان تازه‌ای اعلام نشده است."],
        ),
        (
            _jalali_day(11),
            "چهارشنبه | ۱۱ آذر ۱۴۰۵",
            [
                "↫ امتحان هندسه — مرور جزوه و مطالعهٔ کتاب تا صفحهٔ ۱۶",
                "• نگارش — تکمیل ۳ انشا",
            ],
        ),
        (
            _jalali_day(24),
            "سه‌شنبه | ۲۴ آذر ۱۴۰۵",
            ["↫ امتحان عربی"],
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
        if len(items) == 1 and "تکلیف یا امتحان تازه‌ای اعلام نشده" in items[0]:
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
    """Upsert one announcement and enqueue idempotent 8-hour reminders."""
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
    expiry = datetime.combine(
        _jalali_day(24),
        time(23, 59, 59),
        tzinfo=zone,
    ).astimezone(timezone.utc)
    full_body = build_class104_plan_body(datetime.now(zone).date() + timedelta(days=1))

    announcement = await session.scalar(
        select(Announcement)
        .where(
            Announcement.class_id == class_group.id,
            Announcement.title == ANNOUNCEMENT_TITLE,
        )
        .order_by(Announcement.id.desc())
        .limit(1)
    )
    content_changed = announcement is None
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
            await session.execute(
                update(ReminderJob)
                .where(
                    ReminderJob.entity_type == EntityType.ANNOUNCEMENT.value,
                    ReminderJob.entity_id == announcement.id,
                    ReminderJob.status.in_([ReminderStatus.PENDING, ReminderStatus.RETRY]),
                )
                .values(status=ReminderStatus.CANCELLED, locked_at=None)
            )
            announcement.body = full_body
            announcement.published_at = now
            announcement.expires_at = expiry
            announcement.is_active = True
            await session.flush()

    if expiry <= now:
        announcement.is_active = False
        return {
            "class_id": class_group.id,
            "announcement_id": announcement.id,
            "reminder_jobs": 0,
        }

    anchor = _utc(announcement.published_at)
    elapsed = max(0, int((now - anchor).total_seconds()))
    if content_changed:
        first_slot = 0
    else:
        first_slot = (elapsed + INTERVAL_SECONDS - 1) // INTERVAL_SECONDS

    last_slot = int((expiry - anchor).total_seconds() // INTERVAL_SECONDS)
    if first_slot > last_slot:
        return {
            "class_id": class_group.id,
            "announcement_id": announcement.id,
            "reminder_jobs": 0,
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

    values: list[dict[str, object]] = []
    for slot in range(first_slot, last_slot + 1):
        scheduled_at = anchor + REMINDER_INTERVAL * slot
        if scheduled_at < now - timedelta(minutes=1) or scheduled_at > expiry:
            continue
        local_date = scheduled_at.astimezone(zone).date()
        body = build_class104_plan_body(local_date)
        if not body:
            continue
        message = f"⌯ یادآوری برنامهٔ کلاس ۱۰۴\n\n{body}"
        slot_ts = int(scheduled_at.timestamp())
        for target_type, target_id, recipient_id in targets:
            values.append(
                {
                    "class_id": class_group.id,
                    "entity_type": EntityType.ANNOUNCEMENT.value,
                    "entity_id": announcement.id,
                    "target_type": target_type,
                    "target_id": target_id,
                    "message_text": message,
                    "scheduled_at": scheduled_at,
                    "status": ReminderStatus.PENDING,
                    "attempts": 0,
                    "dedupe_key": (
                        f"classplan:{announcement.id}:{slot_ts}:{target_type}:{target_id}"
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
