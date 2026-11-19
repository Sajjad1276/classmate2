from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from classmate.core.enums import RecordStatus, ReminderStatus, Role
from classmate.db.base import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    private_chat_started: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class School(TimestampMixin, Base):
    __tablename__ = "schools"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Tehran", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class SchoolMembership(TimestampMixin, Base):
    __tablename__ = "school_memberships"
    __table_args__ = (UniqueConstraint("school_id", "user_id", name="uq_school_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[Role] = mapped_column(
        Enum(Role, name="school_role", native_enum=False), default=Role.SCHOOL_ADMIN, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AcademicYear(TimestampMixin, Base):
    __tablename__ = "academic_years"
    __table_args__ = (UniqueConstraint("school_id", "name", name="uq_school_academic_year"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ClassGroup(TimestampMixin, Base):
    __tablename__ = "classes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    academic_year_id: Mapped[int] = mapped_column(
        ForeignKey("academic_years.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    grade: Mapped[str] = mapped_column(String(32), nullable=False)
    section: Mapped[str | None] = mapped_column(String(32))
    group_chat_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True)
    group_chat_title: Mapped[str | None] = mapped_column(String(180))
    group_link_code_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    group_link_code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    member_join_code_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    member_join_code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ClassMembership(TimestampMixin, Base):
    __tablename__ = "class_memberships"
    __table_args__ = (UniqueConstraint("class_id", "user_id", name="uq_class_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[Role] = mapped_column(
        Enum(Role, name="class_role", native_enum=False), default=Role.STUDENT, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Subject(TimestampMixin, Base):
    __tablename__ = "subjects"
    __table_args__ = (UniqueConstraint("school_id", "normalized_name", name="uq_school_subject"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(120), nullable=False)


class ClassSubject(TimestampMixin, Base):
    __tablename__ = "class_subjects"
    __table_args__ = (UniqueConstraint("class_id", "subject_id", name="uq_class_subject"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id", ondelete="CASCADE"), index=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"), index=True)


class TimetableEntry(TimestampMixin, Base):
    __tablename__ = "timetable_entries"
    __table_args__ = (
        UniqueConstraint("class_id", "weekday", "period", name="uq_class_weekday_period"),
        Index("ix_timetable_class_weekday", "class_id", "weekday"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id", ondelete="CASCADE"))
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"))
    # Persian school week: 0=Saturday ... 6=Friday
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    period: Mapped[int] = mapped_column(Integer, nullable=False)
    starts_at: Mapped[time | None] = mapped_column(Time)
    ends_at: Mapped[time | None] = mapped_column(Time)
    room: Mapped[str | None] = mapped_column(String(80))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_until: Mapped[date | None] = mapped_column(Date)


class LessonSession(TimestampMixin, Base):
    __tablename__ = "lesson_sessions"
    __table_args__ = (
        Index("ix_lesson_class_start", "class_id", "starts_at"),
        UniqueConstraint("class_id", "subject_id", "starts_at", name="uq_lesson_class_subject_start"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id", ondelete="CASCADE"))
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), default="scheduled", nullable=False)


class DailyReport(TimestampMixin, Base):
    __tablename__ = "daily_reports"
    __table_args__ = (UniqueConstraint("class_id", "report_date", name="uq_daily_report_class_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id", ondelete="CASCADE"), index=True)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    title: Mapped[str | None] = mapped_column(String(180))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[RecordStatus] = mapped_column(
        Enum(RecordStatus, name="record_status", native_enum=False),
        default=RecordStatus.PENDING,
        nullable=False,
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Homework(TimestampMixin, Base):
    __tablename__ = "homeworks"
    __table_args__ = (Index("ix_homework_class_status_due", "class_id", "status", "due_on"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id", ondelete="CASCADE"), index=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"), index=True)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    due_on: Mapped[date | None] = mapped_column(Date)
    due_session_id: Mapped[int | None] = mapped_column(ForeignKey("lesson_sessions.id"))
    status: Mapped[RecordStatus] = mapped_column(
        Enum(RecordStatus, name="record_status", native_enum=False),
        default=RecordStatus.PENDING,
        nullable=False,
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    subject: Mapped["Subject"] = relationship()


class Exam(TimestampMixin, Base):
    __tablename__ = "exams"
    __table_args__ = (Index("ix_exam_class_status_scheduled", "class_id", "status", "scheduled_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id", ondelete="CASCADE"), index=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"), index=True)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    scope: Mapped[str] = mapped_column(Text, default="", nullable=False)
    exam_type: Mapped[str] = mapped_column(String(40), default="quiz", nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[RecordStatus] = mapped_column(
        Enum(RecordStatus, name="record_status", native_enum=False),
        default=RecordStatus.PENDING,
        nullable=False,
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    subject: Mapped["Subject"] = relationship()


class ApprovalEvent(Base):
    __tablename__ = "approval_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_class_created", "class_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id", ondelete="CASCADE"), index=True)
    class_id: Mapped[int | None] = mapped_column(ForeignKey("classes.id", ondelete="CASCADE"))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[int | None] = mapped_column(Integer)
    before_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NotificationPreference(TimestampMixin, Base):
    __tablename__ = "notification_preferences"
    __table_args__ = (UniqueConstraint("user_id", "class_id", name="uq_notification_user_class"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id", ondelete="CASCADE"), index=True)
    homework_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    exams_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ReminderJob(Base):
    __tablename__ = "reminder_jobs"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_reminder_dedupe_key"),
        Index("ix_reminder_due_status", "status", "scheduled_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id", ondelete="CASCADE"), index=True)
    entity_type: Mapped[str] = mapped_column(String(24), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)  # group | private
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[ReminderStatus] = mapped_column(
        Enum(ReminderStatus, name="reminder_status", native_enum=False),
        default=ReminderStatus.PENDING,
        nullable=False,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dedupe_key: Mapped[str] = mapped_column(String(180), nullable=False)


class ProcessedUpdate(Base):
    __tablename__ = "processed_updates"

    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
