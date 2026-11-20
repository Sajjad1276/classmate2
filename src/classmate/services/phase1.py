"""Phase-one application services: dashboard, calendar, search and announcements."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.core.enums import EntityType, RecordStatus
from classmate.db.models import (
    Announcement,
    ClassGroup,
    ClassMembership,
    DailyReport,
    Exam,
    Homework,
    NotificationPreference,
    Subject,
    TimetableEntry,
    User,
)
from classmate.services.permissions import is_class_manager


async def dashboard_data(session: AsyncSession, class_ids: list[int], today: date) -> dict:
    if not class_ids:
        return {"homework": [], "exams": [], "today_lessons": [], "announcements": []}
    horizon = today + timedelta(days=7)
    homework = list((await session.execute(
        select(Homework, Subject, ClassGroup)
        .join(Subject, Homework.subject_id == Subject.id)
        .join(ClassGroup, Homework.class_id == ClassGroup.id)
        .where(Homework.class_id.in_(class_ids), Homework.status == RecordStatus.APPROVED,
               Homework.due_on >= today, Homework.due_on <= horizon)
        .order_by(Homework.due_on, Homework.created_at)
        .limit(10)
    )).all())
    exams = list((await session.execute(
        select(Exam, Subject, ClassGroup)
        .join(Subject, Exam.subject_id == Subject.id)
        .join(ClassGroup, Exam.class_id == ClassGroup.id)
        .where(Exam.class_id.in_(class_ids), Exam.status == RecordStatus.APPROVED,
               Exam.scheduled_at >= datetime.now(timezone.utc),
               Exam.scheduled_at <= datetime.now(timezone.utc) + timedelta(days=7))
        .order_by(Exam.scheduled_at)
        .limit(10)
    )).all())
    announcements = list((await session.execute(
        select(Announcement, ClassGroup)
        .join(ClassGroup, Announcement.class_id == ClassGroup.id)
        .where(Announcement.class_id.in_(class_ids), Announcement.is_active.is_(True))
        .order_by(Announcement.published_at.desc()).limit(5)
    )).all())
    return {"homework": homework, "exams": exams, "announcements": announcements}


async def calendar_rows(session: AsyncSession, class_id: int, start: date, days: int = 7):
    end = start + timedelta(days=days)
    timetable = list((await session.execute(
        select(TimetableEntry, Subject)
        .join(Subject, TimetableEntry.subject_id == Subject.id)
        .where(TimetableEntry.class_id == class_id)
        .order_by(TimetableEntry.weekday, TimetableEntry.period)
    )).all())
    exams = list((await session.execute(
        select(Exam, Subject).join(Subject, Exam.subject_id == Subject.id)
        .where(Exam.class_id == class_id, Exam.status == RecordStatus.APPROVED,
               Exam.scheduled_at >= datetime.combine(start, datetime.min.time()),
               Exam.scheduled_at < datetime.combine(end, datetime.min.time()))
        .order_by(Exam.scheduled_at)
    )).all())
    homeworks = list((await session.execute(
        select(Homework, Subject).join(Subject, Homework.subject_id == Subject.id)
        .where(Homework.class_id == class_id, Homework.status == RecordStatus.APPROVED,
               Homework.due_on >= start, Homework.due_on < end)
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
    record = await session.get(model, entity_id)
    if record is None:
        return False, "اطلاعات موردنظر پیدا نشد."
    if not await is_class_manager(session, actor, record.class_id):
        return False, "فقط مدیر کلاس می‌تواند اطلاعات را آرشیو کند."
    if record.status == RecordStatus.CANCELLED:
        return False, "این اطلاعات قبلاً آرشیو شده است."
    record.status = RecordStatus.CANCELLED
    return True, "اطلاعات با موفقیت آرشیو شد."


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
