"""Phase-one application services: dashboard, calendar, search and announcements."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.core.enums import EntityType, OutboxStatus, RecordStatus, ReminderStatus
from classmate.db.models import (
    AcademicYear,
    Announcement,
    ClassGroup,
    DailyReport,
    Exam,
    Homework,
    NotificationPreference,
    ReminderJob,
    School,
    Subject,
    TelegramOutbox,
    TimetableEntry,
    User,
)
from classmate.services.permissions import is_class_manager
from classmate.domain.dates import persian_weekday


async def dashboard_data(session: AsyncSession, class_ids: list[int], today: date | None = None) -> dict:
    if not class_ids:
        return {"homework": [], "exams": [], "today_lessons": [], "announcements": []}

    contexts = (
        await session.execute(
            select(ClassGroup.id, School.timezone)
            .join(AcademicYear, ClassGroup.academic_year_id == AcademicYear.id)
            .join(School, AcademicYear.school_id == School.id)
            .where(ClassGroup.id.in_(class_ids), ClassGroup.is_active.is_(True), School.is_active.is_(True))
        )
    ).all()
    homework_rows = []
    exam_rows = []
    lesson_rows = []
    announcement_rows = []
    now_utc = datetime.now(timezone.utc)

    for class_id, timezone_name in contexts:
        zone = ZoneInfo(timezone_name)
        now_local = datetime.now(zone)
        local_today = today or now_local.date()
        local_horizon = local_today + timedelta(days=7)
        exam_horizon = now_local + timedelta(days=7)

        homework_rows.extend((await session.execute(
            select(Homework, Subject, ClassGroup)
            .join(Subject, Homework.subject_id == Subject.id)
            .join(ClassGroup, Homework.class_id == ClassGroup.id)
            .where(
                Homework.class_id == class_id,
                Homework.status == RecordStatus.APPROVED,
                Homework.due_on >= local_today,
                Homework.due_on <= local_horizon,
            )
            .order_by(Homework.due_on, Homework.created_at)
            .limit(10)
        )).all())

        exam_rows.extend((await session.execute(
            select(Exam, Subject, ClassGroup)
            .join(Subject, Exam.subject_id == Subject.id)
            .join(ClassGroup, Exam.class_id == ClassGroup.id)
            .where(
                Exam.class_id == class_id,
                Exam.status == RecordStatus.APPROVED,
                Exam.scheduled_at >= now_local,
                Exam.scheduled_at <= exam_horizon,
            )
            .order_by(Exam.scheduled_at)
            .limit(10)
        )).all())

        lesson_rows.extend((await session.execute(
            select(TimetableEntry, Subject, ClassGroup)
            .join(Subject, TimetableEntry.subject_id == Subject.id)
            .join(ClassGroup, TimetableEntry.class_id == ClassGroup.id)
            .where(
                TimetableEntry.class_id == class_id,
                TimetableEntry.weekday == persian_weekday(local_today),
                or_(TimetableEntry.valid_from.is_(None), TimetableEntry.valid_from <= local_today),
                or_(TimetableEntry.valid_until.is_(None), TimetableEntry.valid_until >= local_today),
            )
            .order_by(TimetableEntry.period)
            .limit(20)
        )).all())

        announcement_rows.extend((await session.execute(
            select(Announcement, ClassGroup)
            .join(ClassGroup, Announcement.class_id == ClassGroup.id)
            .where(
                Announcement.class_id == class_id,
                Announcement.is_active.is_(True),
                or_(Announcement.expires_at.is_(None), Announcement.expires_at > now_utc),
            )
            .order_by(Announcement.published_at.desc())
            .limit(5)
        )).all())

    homework_rows.sort(key=lambda row: (row[0].due_on or date.max, row[0].created_at))
    exam_rows.sort(key=lambda row: row[0].scheduled_at)
    lesson_rows.sort(key=lambda row: (row[2].name, row[0].weekday, row[0].period))
    announcement_rows.sort(key=lambda row: row[0].published_at, reverse=True)
    return {
        "homework": homework_rows[:10],
        "exams": exam_rows[:10],
        "today_lessons": lesson_rows[:20],
        "announcements": announcement_rows[:5],
    }


async def calendar_rows(
    session: AsyncSession,
    class_id: int,
    start: date,
    days: int = 7,
    timezone_name: str = "Asia/Tehran",
):
    zone = ZoneInfo(timezone_name)
    end = start + timedelta(days=days)
    start_at = datetime.combine(start, datetime.min.time(), tzinfo=zone)
    end_at = datetime.combine(end, datetime.min.time(), tzinfo=zone)
    timetable = list((await session.execute(
        select(TimetableEntry, Subject)
        .join(Subject, TimetableEntry.subject_id == Subject.id)
        .where(
            TimetableEntry.class_id == class_id,
            or_(TimetableEntry.valid_until.is_(None), TimetableEntry.valid_until >= start),
            or_(TimetableEntry.valid_from.is_(None), TimetableEntry.valid_from < end),
        )
        .order_by(TimetableEntry.weekday, TimetableEntry.period)
    )).all())
    exams = list((await session.execute(
        select(Exam, Subject).join(Subject, Exam.subject_id == Subject.id)
        .where(
            Exam.class_id == class_id,
            Exam.status == RecordStatus.APPROVED,
            Exam.scheduled_at >= start_at,
            Exam.scheduled_at < end_at,
        )
        .order_by(Exam.scheduled_at)
    )).all())
    homeworks = list((await session.execute(
        select(Homework, Subject).join(Subject, Homework.subject_id == Subject.id)
        .where(
            Homework.class_id == class_id,
            Homework.status == RecordStatus.APPROVED,
            Homework.due_on >= start,
            Homework.due_on < end,
        )
        .order_by(Homework.due_on)
    )).all())
    return timetable, exams, homeworks

async def search_records(session: AsyncSession, class_ids: list[int], query: str):
    term = f"%{query.strip()}%"
    if not class_ids or not query.strip():
        return []
    homework = list((await session.execute(
        select(Homework, Subject, ClassGroup).join(Subject, Homework.subject_id == Subject.id)
        .join(ClassGroup, Homework.class_id == ClassGroup.id)
        .where(Homework.class_id.in_(class_ids), Homework.status == RecordStatus.APPROVED,
               or_(Homework.title.ilike(term), Homework.description.ilike(term), Subject.name.ilike(term)))
        .order_by(Homework.created_at.desc()).limit(20)
    )).all())
    exams = list((await session.execute(
        select(Exam, Subject, ClassGroup).join(Subject, Exam.subject_id == Subject.id)
        .join(ClassGroup, Exam.class_id == ClassGroup.id)
        .where(Exam.class_id.in_(class_ids), Exam.status == RecordStatus.APPROVED,
               or_(Exam.title.ilike(term), Exam.scope.ilike(term), Subject.name.ilike(term)))
        .order_by(Exam.scheduled_at.desc()).limit(20)
    )).all())
    reports = list((await session.execute(
        select(DailyReport, ClassGroup).join(ClassGroup, DailyReport.class_id == ClassGroup.id)
        .where(DailyReport.class_id.in_(class_ids), DailyReport.status == RecordStatus.APPROVED,
               or_(DailyReport.title.ilike(term), DailyReport.body.ilike(term)))
        .order_by(DailyReport.created_at.desc()).limit(20)
    )).all())
    return [("homework", row) for row in homework] + [("exam", row) for row in exams] + [("report", row) for row in reports]


async def archive_record(session: AsyncSession, entity_type: str, entity_id: int, actor: User) -> tuple[bool, str]:
    model = {EntityType.HOMEWORK.value: Homework, EntityType.EXAM.value: Exam,
             EntityType.REPORT.value: DailyReport}.get(entity_type)
    if model is None:
        return False, "نوع اطلاعات معتبر نیست."
    record = await session.get(model, entity_id, with_for_update=True)
    if record is None:
        return False, "اطلاعات موردنظر پیدا نشد."
    if not await is_class_manager(session, actor, record.class_id):
        return False, "فقط مدیر کلاس می‌تواند اطلاعات را آرشیو کند."
    if record.status == RecordStatus.CANCELLED:
        return False, "این اطلاعات قبلاً آرشیو شده است."
    record.status = RecordStatus.CANCELLED
    await session.execute(
        update(TelegramOutbox)
        .where(
            TelegramOutbox.entity_type == entity_type,
            TelegramOutbox.entity_id == entity_id,
            TelegramOutbox.status.in_(
                [OutboxStatus.PENDING, OutboxStatus.RETRY, OutboxStatus.PROCESSING]
            ),
        )
        .values(status=OutboxStatus.CANCELLED, locked_at=None)
    )
    if entity_type in {EntityType.HOMEWORK.value, EntityType.EXAM.value}:
        from classmate.services.records import set_reminders_cancelled

        await set_reminders_cancelled(session, entity_type, entity_id)
    await session.flush()
    return True, "اطلاعات با موفقیت آرشیو شد."


async def update_record(
    session: AsyncSession,
    entity_type: str,
    entity_id: int,
    first: str,
    second: str,
    actor: User,
) -> tuple[bool, str]:
    model = {EntityType.HOMEWORK.value: Homework, EntityType.EXAM.value: Exam,
             EntityType.REPORT.value: DailyReport}.get(entity_type)
    if model is None:
        return False, "نوع اطلاعات معتبر نیست."
    record = await session.get(model, entity_id, with_for_update=True)
    if record is None:
        return False, "اطلاعات موردنظر پیدا نشد."
    if not await is_class_manager(session, actor, record.class_id):
        return False, "فقط مدیر کلاس می‌تواند اطلاعات را ویرایش کند."
    if record.status == RecordStatus.CANCELLED:
        return False, "اطلاعات آرشیوشده قابل ویرایش نیست."
    new_title = first.strip()[:180]
    if not new_title:
        return False, "عنوان نمی‌تواند خالی باشد."
    old_title = record.title or ""
    if old_title and old_title != new_title:
        pending_reminders = (
            await session.scalars(
                select(ReminderJob)
                .where(
                    ReminderJob.entity_type == entity_type,
                    ReminderJob.entity_id == entity_id,
                    ReminderJob.status.in_(
                        [ReminderStatus.PENDING, ReminderStatus.RETRY, ReminderStatus.PROCESSING]
                    ),
                )
                .with_for_update()
            )
        ).all()
        for reminder in pending_reminders:
            reminder.message_text = reminder.message_text.replace(old_title, new_title)

        pending_messages = (
            await session.scalars(
                select(TelegramOutbox)
                .where(
                    TelegramOutbox.entity_type == entity_type,
                    TelegramOutbox.entity_id == entity_id,
                    TelegramOutbox.status.in_(
                        [OutboxStatus.PENDING, OutboxStatus.RETRY, OutboxStatus.PROCESSING]
                    ),
                )
                .with_for_update()
            )
        ).all()
        for queued_message in pending_messages:
            queued_message.text = queued_message.text.replace(old_title, new_title)

    record.title = new_title
    if entity_type == EntityType.HOMEWORK.value:
        record.description = second.strip()
    elif entity_type == EntityType.EXAM.value:
        record.scope = second.strip()
    else:
        record.body = second.strip()
    return True, "اطلاعات با موفقیت ویرایش شد."


async def get_preferences(session: AsyncSession, user_id: int, class_id: int) -> NotificationPreference:
    pref = await session.scalar(select(NotificationPreference).where(
        NotificationPreference.user_id == user_id, NotificationPreference.class_id == class_id))
    if pref is None:
        pref = NotificationPreference(user_id=user_id, class_id=class_id)
        session.add(pref)
        await session.flush()
    return pref


async def list_announcements(session: AsyncSession, class_ids: list[int], limit: int = 15):
    now = datetime.now(timezone.utc)
    return list((await session.execute(
        select(Announcement, ClassGroup).join(ClassGroup, Announcement.class_id == ClassGroup.id)
        .where(Announcement.class_id.in_(class_ids), Announcement.is_active.is_(True),
               or_(Announcement.expires_at.is_(None), Announcement.expires_at > now))
        .order_by(Announcement.published_at.desc()).limit(limit)
    )).all())


async def create_announcement(session: AsyncSession, class_id: int, title: str, body: str, user: User) -> Announcement:
    if not await is_class_manager(session, user, class_id):
        raise PermissionError("ایجاد اطلاعیه فقط برای مدیر کلاس مجاز است.")
    item = Announcement(class_id=class_id, title=title.strip()[:180], body=body.strip(), created_by=user.id)
    session.add(item)
    await session.flush()
    return item
