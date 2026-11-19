"""Central enums for roles and record lifecycle."""

from enum import StrEnum


class Role(StrEnum):
    """Membership roles scoped to school or class."""

    PLATFORM_ADMIN = "platform_admin"
    SCHOOL_ADMIN = "school_admin"
    CLASS_MANAGER = "class_manager"
    TRUSTED_APPROVER = "trusted_approver"
    STUDENT = "student"


class RecordStatus(StrEnum):
    """Lifecycle of homework, exam and daily report records."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REVISION_REQUESTED = "REVISION_REQUESTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class ReminderStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SENT = "sent"
    RETRY = "retry"
    CANCELLED = "cancelled"
    FAILED = "failed"


class EntityType(StrEnum):
    HOMEWORK = "homework"
    EXAM = "exam"
    REPORT = "report"
