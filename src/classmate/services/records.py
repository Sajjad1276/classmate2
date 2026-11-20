"""Homework, exam, report creation and approval."""

from datetime import date, datetime, time, timedelta
from html import escape
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.core.enums import EntityType, OutboxStatus, RecordStatus
from classmate.db.models import (
    AcademicYear,
    ApprovalEvent,
    AuditLog,
    ClassGroup,
    ClassSubject,
    DailyReport,
    Exam,
    Homework,
    LessonSession,
    ReminderJob,
    TelegramOutbox,
    School,
    Subject,
    TimetableEntry,
    User,
)
from classmate.domain.dates import jalali_date, jalali_datetime, persian_weekday
from classmate.services.outbox import enqueue_telegram_message
from classmate.services.notifications import schedule_approved_record_reminders
from classmate.services.permissions import can_approve, get_class_with_school, is_class_manager


def normalize_subject_name(value: str) -> str:
    return " ".join(
        value.replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ").split()
    ).casefold()


async def get_group_context(session: AsyncSession, telegram_chat_id: int):
    return (
        await session.execute(
            select(ClassGroup, AcademicYear, School)
            .join(AcademicYear, ClassGroup.academic_year_id == AcademicYear.id)
            .join(School, AcademicYear.school_id == School.id)
            .where(
                ClassGroup.group_chat_id == telegram_chat_id,
                ClassGroup.is_active.is_(True),
                School.is_active.is_(True),
            )
        )
    ).first()


async def get_subject(
    session: AsyncSession, class_id: int, school_id: int, subject_name: str
) -> Subject | None:
    normalized = normalize_subject_name(subject_name)
    return await session.scalar(
        select(Subject)
        .join(ClassSubject, ClassSubject.subject_id == Subject.id)
        .where(
            ClassSubject.class_id == class_id,
            Subject.school_id == school_id,
            Subject.normalized_name == normalized,
        )
    )


async def add_subject(
    session: AsyncSession, class_id: int, school_id: int, name: str
) -> Subject:
    actual_school_id = await session.scalar(
        select(AcademicYear.school_id)
        .join(ClassGroup, ClassGroup.academic_year_id == AcademicYear.id)
        .join(School, AcademicYear.school_id == School.id)
        .where(
            ClassGroup.id == class_id,
            ClassGroup.is_active.is_(True),
            School.is_active.is_(True),
        )
    )
    if actual_school_id != school_id:
        raise ValueError("مدرسهٔ درس با مدرسهٔ کلاس مطابقت ندارد یا غیرفعال است.")
    normalized = normalize_subject_name(name)
    if not normalized:
        raise ValueError("نام درس نمی‌تواند خالی باشد.")
    subject = await session.scalar(
        select(Subject).where(
            Subject.school_id == school_id,
            Subject.normalized_name == normalized,
        )
    )
    if subject is None:
        subject = Subject(school_id=school_id, name=name.strip(), normalized_name=normalized)
        session.add(subject)
        await session.flush()
    link = await session.scalar(
        select(ClassSubject).where(
            ClassSubject.class_id == class_id, ClassSubject.subject_id == subject.id
        )
    )
    if link is None:
        session.add(ClassSubject(class_id=class_id, subject_id=subject.id))
    await session.flush()
    return subject


async def _validate_subject_for_class(
    session: AsyncSession, class_id: int, subject: Subject
) -> None:
    row = await session.execute(
        select(ClassSubject.id, Subject.school_id, AcademicYear.school_id)
        .join(Subject, ClassSubject.subject_id == Subject.id)
        .join(ClassGroup, ClassSubject.class_id == ClassGroup.id)
        .join(AcademicYear, ClassGroup.academic_year_id == AcademicYear.id)
        .join(School, AcademicYear.school_id == School.id)
        .where(
            ClassSubject.class_id == class_id,
            ClassSubject.subject_id == subject.id,
            ClassGroup.is_active.is_(True),
            School.is_active.is_(True),
        )
    )
    linked = row.first()
    if linked is None or linked[1] != linked[2]:
        raise ValueError("درس انتخاب‌شده متعلق به این کلاس نیست.")


async def get_or_create_next_lesson(
    session: AsyncSession,
    class_id: int,
    subject_id: int,
    timezone_name: str,
) -> LessonSession | None:
    zone = ZoneInfo(timezone_name)
    now = datetime.now(zone)
    entries = (
        await session.execute(
            select(TimetableEntry).where(
                TimetableEntry.class_id == class_id,
                TimetableEntry.subject_id == subject_id,
            )
        )
    ).scalars().all()

    occurrences: list[tuple[datetime, datetime | None]] = []
    for entry in entries:
        for offset in range(15):
            lesson_date = now.date() + timedelta(days=offset)
            if persian_weekday(lesson_date) != entry.weekday:
                continue
            if entry.valid_from and lesson_date < entry.valid_from:
                continue
            if entry.valid_until and lesson_date > entry.valid_until:
                continue
            lesson_start = datetime.combine(
                lesson_date, entry.starts_at or time(8, 0), tzinfo=zone
            )
            if lesson_start <= now:
                continue
            lesson_end = (
                datetime.combine(lesson_date, entry.ends_at, tzinfo=zone)
                if entry.ends_at
                else None
            )
            occurrences.append((lesson_start, lesson_end))
            break

    if not occurrences:
        return None

    starts_at, ends_at = min(occurrences, key=lambda item: item[0])
    existing = await session.scalar(
        select(LessonSession).where(
            LessonSession.class_id == class_id,
            LessonSession.subject_id == subject_id,
            LessonSession.starts_at == starts_at,
        )
    )
    if existing is not None:
        return existing

    lesson = LessonSession(
        class_id=class_id,
        subject_id=subject_id,
        starts_at=starts_at,
        ends_at=ends_at,
        status="scheduled",
    )
    session.add(lesson)
    await session.flush()
    return lesson




async def _require_active_creator(session: AsyncSession, user_id: int) -> None:
    active_user = await session.scalar(
        select(User.id).where(User.id == user_id, User.is_active.is_(True))
    )
    if active_user is None:
        raise PermissionError("حساب ثبت‌کننده فعال نیست.")




async def _creation_approval_context(
    session: AsyncSession,
    class_id: int,
    created_by: int,
    *,
    include_trusted_approver: bool = False,
) -> tuple[bool, int, str]:
    """Return whether the creator has direct publishing authority and the school context."""
    context = await get_class_with_school(session, class_id)
    if context is None:
        raise ValueError("کلاس یا مدرسه فعال نیست.")
    creator = await session.get(User, created_by)
    if creator is None or not creator.is_active:
        raise PermissionError("حساب ثبت‌کننده فعال نیست.")
    school = context[2]
    publisher = (
        await can_approve(session, creator, class_id)
        if include_trusted_approver
        else await is_class_manager(session, creator, class_id)
    )
    return publisher, school.id, school.timezone


async def _queue_approved_record_publication(
    session: AsyncSession,
    entity_type: str,
    record: Homework | Exam | DailyReport,
    timezone_name: str,
) -> bool:
    """Publish an approved record to the linked class group, never a pending one."""
    if record.status != RecordStatus.APPROVED:
        return False
    context = await get_class_with_school(session, record.class_id)
    if context is None:
        return False
    class_group = context[0]
    if class_group.group_chat_id is None:
        return False

    creator = await session.get(User, record.created_by)
    creator_name = escape(creator.full_name if creator is not None else "عضو کلاس")
    lines: list[str]
    if entity_type == EntityType.HOMEWORK.value and isinstance(record, Homework):
        subject = await session.get(Subject, record.subject_id)
        due = jalali_date(record.due_on) if record.due_on else "بدون موعد"
        lines = [
            "📚 <b>تکلیف جدید منتشر شد</b>",
            f"کلاس: {escape(class_group.name)}",
            f"درس: {escape(subject.name if subject is not None else '—')}",
            f"عنوان: {escape(record.title)}",
            f"توضیحات: {escape(record.description[:1600])}",
            f"موعد: {due}",
            f"ثبت‌کننده: {creator_name}",
        ]
    elif entity_type == EntityType.EXAM.value and isinstance(record, Exam):
        subject = await session.get(Subject, record.subject_id)
        lines = [
            "📝 <b>امتحان جدید منتشر شد</b>",
            f"کلاس: {escape(class_group.name)}",
            f"درس: {escape(subject.name if subject is not None else '—')}",
            f"عنوان: {escape(record.title)}",
            f"محدوده: {escape(record.scope[:1200] or '—')}",
            f"زمان: {jalali_datetime(record.scheduled_at, timezone_name)}",
            f"ثبت‌کننده: {creator_name}",
        ]
    elif entity_type == EntityType.REPORT.value and isinstance(record, DailyReport):
        lines = [
            "📋 <b>گزارش روزانه منتشر شد</b>",
            f"کلاس: {escape(class_group.name)}",
            f"تاریخ: {jalali_date(record.report_date)}",
            f"عنوان: {escape((record.title or 'گزارش روزانه')[:180])}",
            escape(record.body[:2200]),
            f"ثبت‌کننده: {creator_name}",
        ]
    else:
        return False

    await enqueue_telegram_message(
        session,
        target_type="group",
        target_chat_id=class_group.group_chat_id,
        class_id=class_group.id,
        text="\n".join(lines),
        entity_type=entity_type,
        entity_id=record.id,
        expected_record_status=RecordStatus.APPROVED.value,
        idempotency_key=f"publish:{entity_type}:{record.id}",
    )
    return True


async def _record_auto_approval(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: int,
    class_id: int,
    school_id: int,
    actor_id: int,
    timezone_name: str,
) -> None:
    """Audit an authorized owner's direct publication and schedule its reminders."""
    session.add(
        ApprovalEvent(
            entity_type=entity_type,
            entity_id=entity_id,
            actor_id=actor_id,
            action="AUTO_APPROVED",
            note="Published automatically because the creator has direct publishing authority.",
        )
    )
    await audit(
        session,
        school_id,
        class_id,
        actor_id,
        "auto_approve",
        entity_type,
        entity_id,
        after_data={"status": RecordStatus.APPROVED.value, "approval": "automatic"},
    )
    if entity_type in {EntityType.HOMEWORK.value, EntityType.EXAM.value}:
        class_group = await session.get(ClassGroup, class_id)
        record_model = Homework if entity_type == EntityType.HOMEWORK.value else Exam
        record = await session.get(record_model, entity_id)
        if class_group is not None and record is not None:
            await schedule_approved_record_reminders(
                session, class_group, record, entity_type, timezone_name
            )
    record_model = {
        EntityType.HOMEWORK.value: Homework,
        EntityType.EXAM.value: Exam,
        EntityType.REPORT.value: DailyReport,
    }[entity_type]
    record = await session.get(record_model, entity_id)
    if record is not None:
        await _queue_approved_record_publication(
            session, entity_type, record, timezone_name
        )


async def create_homework(
    session: AsyncSession,
    class_id: int,
    subject: Subject,
    title: str,
    description: str,
    due_on: date | None,
    created_by: int,
    due_session_id: int | None = None,
) -> Homework:
    await _validate_subject_for_class(session, class_id, subject)
    await _require_active_creator(session, created_by)
    auto_approved, school_id, timezone_name = await _creation_approval_context(
        session, class_id, created_by
    )
    approved_at = datetime.now(ZoneInfo(timezone_name)) if auto_approved else None
    if due_session_id is not None:
        lesson_id = await session.scalar(
            select(LessonSession.id).where(
                LessonSession.id == due_session_id,
                LessonSession.class_id == class_id,
            )
        )
        if lesson_id is None:
            raise ValueError("جلسه انتخاب‌شده متعلق به این کلاس نیست.")
    if not title.strip():
        raise ValueError("عنوان تکلیف نمی‌تواند خالی باشد.")
    record = Homework(
        class_id=class_id,
        subject_id=subject.id,
        title=title.strip()[:180],
        description=description.strip(),
        due_on=due_on,
        due_session_id=due_session_id,
        created_by=created_by,
        status=RecordStatus.APPROVED if auto_approved else RecordStatus.PENDING,
        approved_by=created_by if auto_approved else None,
        approved_at=approved_at,
    )
    session.add(record)
    await session.flush()
    if auto_approved:
        await _record_auto_approval(
            session,
            entity_type=EntityType.HOMEWORK.value,
            entity_id=record.id,
            class_id=class_id,
            school_id=school_id,
            actor_id=created_by,
            timezone_name=timezone_name,
        )
    return record


async def create_exam(
    session: AsyncSession,
    class_id: int,
    subject: Subject,
    title: str,
    scope: str,
    exam_type: str,
    scheduled_at: datetime,
    created_by: int,
) -> Exam:
    await _validate_subject_for_class(session, class_id, subject)
    await _require_active_creator(session, created_by)
    auto_approved, school_id, timezone_name = await _creation_approval_context(
        session, class_id, created_by
    )
    approved_at = datetime.now(ZoneInfo(timezone_name)) if auto_approved else None
    if not title.strip():
        raise ValueError("عنوان امتحان نمی‌تواند خالی باشد.")
    if scheduled_at.tzinfo is None:
        raise ValueError("Exam datetime must be timezone-aware")
    record = Exam(
        class_id=class_id,
        subject_id=subject.id,
        title=title.strip()[:180],
        scope=scope.strip(),
        exam_type=exam_type,
        scheduled_at=scheduled_at,
        created_by=created_by,
        status=RecordStatus.APPROVED if auto_approved else RecordStatus.PENDING,
        approved_by=created_by if auto_approved else None,
        approved_at=approved_at,
    )
    session.add(record)
    await session.flush()
    if auto_approved:
        await _record_auto_approval(
            session,
            entity_type=EntityType.EXAM.value,
            entity_id=record.id,
            class_id=class_id,
            school_id=school_id,
            actor_id=created_by,
            timezone_name=timezone_name,
        )
    return record


async def create_daily_report(
    session: AsyncSession,
    class_id: int,
    report_date: date,
    body: str,
    created_by: int,
    title: str | None = None,
) -> DailyReport:
    await _require_active_creator(session, created_by)
    auto_approved, school_id, timezone_name = await _creation_approval_context(
        session, class_id, created_by, include_trusted_approver=True
    )
    approved_at = datetime.now(ZoneInfo(timezone_name)) if auto_approved else None
    if not body.strip():
        raise ValueError("متن گزارش نمی‌تواند خالی باشد.")
    record = DailyReport(
        class_id=class_id,
        report_date=report_date,
        title=(title or "گزارش روزانه")[:180],
        body=body.strip(),
        created_by=created_by,
        status=RecordStatus.APPROVED if auto_approved else RecordStatus.PENDING,
        approved_by=created_by if auto_approved else None,
        approved_at=approved_at,
    )
    session.add(record)
    await session.flush()
    if auto_approved:
        await _record_auto_approval(
            session,
            entity_type=EntityType.REPORT.value,
            entity_id=record.id,
            class_id=class_id,
            school_id=school_id,
            actor_id=created_by,
            timezone_name=timezone_name,
        )
    return record


async def audit(
    session: AsyncSession,
    school_id: int,
    class_id: int | None,
    actor_id: int,
    action: str,
    entity_type: str,
    entity_id: int,
    after_data: dict | None = None,
    before_data: dict | None = None,
) -> None:
    session.add(
        AuditLog(
            school_id=school_id,
            class_id=class_id,
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before_data=before_data,
            after_data=after_data,
        )
    )


async def _cancel_pending_outbox_for_record(
    session: AsyncSession, entity_type: str, entity_id: int
) -> None:
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


async def approve_record(
    session: AsyncSession,
    entity_type: str,
    entity_id: int,
    actor_id: int,
    school_id: int,
    class_id: int,
    timezone_name: str,
) -> tuple[bool, str]:
    model_map = {
        EntityType.HOMEWORK.value: Homework,
        EntityType.EXAM.value: Exam,
        EntityType.REPORT.value: DailyReport,
    }
    model = model_map.get(entity_type)
    if model is None:
        return False, "نوع اطلاعات پشتیبانی نمی‌شود."

    record = await session.get(model, entity_id, with_for_update=True)
    if record is None or record.class_id != class_id:
        return False, "اطلاعات موردنظر پیدا نشد."
    if record.status != RecordStatus.PENDING:
        return False, f"این مورد در وضعیت {record.status} است و قابل تأیید نیست."
    if record.created_by == actor_id:
        return False, "ثبت‌کننده اجازه تأیید درخواست خودش را ندارد."
    actor = await session.get(User, actor_id)
    if actor is None or not await can_approve(session, actor, class_id):
        return False, "شما اجازه تأیید موارد این کلاس را ندارید."

    record.status = RecordStatus.APPROVED
    await _cancel_pending_outbox_for_record(session, entity_type, entity_id)
    record.approved_by = actor_id
    record.approved_at = datetime.now(ZoneInfo(timezone_name))
    session.add(
        ApprovalEvent(
            entity_type=entity_type,
            entity_id=entity_id,
            actor_id=actor_id,
            action="APPROVED",
        )
    )
    await audit(
        session,
        school_id,
        class_id,
        actor_id,
        "approve",
        entity_type,
        entity_id,
        {"status": "APPROVED"},
    )
    await session.flush()

    if entity_type in {EntityType.HOMEWORK.value, EntityType.EXAM.value}:
        class_group = await session.get(ClassGroup, class_id)
        if class_group:
            await schedule_approved_record_reminders(
                session, class_group, record, entity_type, timezone_name
            )
    await _queue_approved_record_publication(
        session, entity_type, record, timezone_name
    )
    return True, "اطلاعات تأیید شد و برای انتشار آماده شد."


async def reject_record(
    session: AsyncSession,
    entity_type: str,
    entity_id: int,
    actor_id: int,
    school_id: int,
    class_id: int,
    note: str = "",
) -> tuple[bool, str]:
    model_map = {
        EntityType.HOMEWORK.value: Homework,
        EntityType.EXAM.value: Exam,
        EntityType.REPORT.value: DailyReport,
    }
    model = model_map.get(entity_type)
    if model is None:
        return False, "نوع اطلاعات پشتیبانی نمی‌شود."

    record = await session.get(model, entity_id, with_for_update=True)
    if record is None or record.class_id != class_id:
        return False, "اطلاعات موردنظر پیدا نشد."
    if record.status not in {RecordStatus.PENDING, RecordStatus.REVISION_REQUESTED}:
        return False, "این مورد در وضعیت قابل رد نیست."
    if record.created_by == actor_id:
        return False, "ثبت‌کننده اجازه رد درخواست خودش را ندارد."
    actor = await session.get(User, actor_id)
    if actor is None or not await can_approve(session, actor, class_id):
        return False, "شما اجازه رد موارد این کلاس را ندارید."

    record.status = RecordStatus.REJECTED
    await _cancel_pending_outbox_for_record(session, entity_type, entity_id)
    session.add(
        ApprovalEvent(
            entity_type=entity_type,
            entity_id=entity_id,
            actor_id=actor_id,
            action="REJECTED",
            note=note or None,
        )
    )
    await audit(
        session,
        school_id,
        class_id,
        actor_id,
        "reject",
        entity_type,
        entity_id,
        {"status": "REJECTED", "note": note},
    )
    await session.flush()
    return True, "درخواست رد شد."


async def list_homework(
    session: AsyncSession, class_id: int, subject_name: str | None = None
) -> list[tuple[Homework, Subject]]:
    query = (
        select(Homework, Subject)
        .join(Subject, Homework.subject_id == Subject.id)
        .where(Homework.class_id == class_id, Homework.status == RecordStatus.APPROVED)
        .order_by(Homework.due_on.asc().nullslast(), Homework.created_at.desc())
        .limit(30)
    )
    if subject_name:
        query = query.where(Subject.normalized_name == normalize_subject_name(subject_name))
    return list((await session.execute(query)).all())


async def list_exams(session: AsyncSession, class_id: int) -> list[tuple[Exam, Subject]]:
    return list(
        (
            await session.execute(
                select(Exam, Subject)
                .join(Subject, Exam.subject_id == Subject.id)
                .where(Exam.class_id == class_id, Exam.status == RecordStatus.APPROVED)
                .order_by(Exam.scheduled_at.asc())
                .limit(30)
            )
        ).all()
    )


async def list_reports(session: AsyncSession, class_id: int, report_date: date):
    return list(
        (
            await session.execute(
                select(DailyReport)
                .where(
                    DailyReport.class_id == class_id,
                    DailyReport.report_date == report_date,
                    DailyReport.status == RecordStatus.APPROVED,
                )
                .order_by(DailyReport.created_at.desc())
            )
        ).scalars().all()
    )


async def list_pending_records(session: AsyncSession, class_id: int):
    pending = []
    for label, model in (
        (EntityType.HOMEWORK.value, Homework),
        (EntityType.EXAM.value, Exam),
        (EntityType.REPORT.value, DailyReport),
    ):
        records = (
            await session.execute(
                select(model)
                .where(model.class_id == class_id, model.status == RecordStatus.PENDING)
                .order_by(model.created_at.asc())
                .limit(15)
            )
        ).scalars().all()
        pending.extend((label, record) for record in records)
    return pending


async def set_reminders_cancelled(
    session: AsyncSession, entity_type: str, entity_id: int
) -> None:
    from classmate.core.enums import ReminderStatus

    jobs = (
        await session.execute(
            select(ReminderJob).where(
                ReminderJob.entity_type == entity_type,
                ReminderJob.entity_id == entity_id,
                ReminderJob.status.in_([ReminderStatus.PENDING, ReminderStatus.RETRY]),
            )
        )
    ).scalars().all()
    for job in jobs:
        job.status = ReminderStatus.CANCELLED
