"""Transactional application operations for the ClassMate management bot."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.core.enums import Role, RecordStatus
from classmate.db.models import (
    AdminAccount,
    AdminAuditEvent,
    AdminDashboardPreference,
    Announcement,
    Attendance,
    ClassGroup,
    ClassMembership,
    ClassSubject,
    DailyReport,
    Exam,
    Grade,
    Homework,
    HomeworkSubmission,
    OutboxStatus,
    School,
    SchoolMembership,
    Subject,
    TelegramOutbox,
    User,
)
from classmate.services.permissions import code_expiry, hash_code, make_code


DEFAULT_DASHBOARD_CARDS = [
    "users",
    "growth",
    "schools",
    "classes",
    "groups",
    "content",
    "files",
    "delivery",
    "health",
]


async def audit_action(
    session: AsyncSession,
    *,
    actor_telegram_id: int,
    action: str,
    target_type: str,
    target_id: int | str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    session.add(
        AdminAuditEvent(
            actor_telegram_id=actor_telegram_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            before_data=before,
            after_data=after,
        )
    )


async def ensure_admin_user(session: AsyncSession, telegram_user, *, bootstrap: bool) -> User:
    user = await session.scalar(select(User).where(User.telegram_id == telegram_user.id))
    full_name = (
        getattr(telegram_user, "full_name", None)
        or getattr(telegram_user, "first_name", None)
        or "مدیر ClassMate"
    )
    if user is None:
        user = User(
            telegram_id=telegram_user.id,
            username=getattr(telegram_user, "username", None),
            full_name=full_name[:160],
            private_chat_started=True,
            is_platform_admin=bootstrap,
            is_active=True,
        )
        session.add(user)
        await session.flush()
    else:
        user.username = getattr(telegram_user, "username", None)
        user.full_name = full_name[:160]
        user.private_chat_started = True
        if bootstrap:
            user.is_platform_admin = True
        await session.flush()
    return user


async def dashboard_stats(session: AsyncSession) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    counts: dict[str, Any] = {}
    counts["users_total"] = await session.scalar(select(func.count(User.id))) or 0
    counts["users_active"] = await session.scalar(
        select(func.count(User.id)).where(User.is_active.is_(True))
    ) or 0
    for key, delta in (
        ("1d", timedelta(days=1)),
        ("7d", timedelta(days=7)),
        ("30d", timedelta(days=30)),
        ("1h", timedelta(hours=1)),
        ("4h", timedelta(hours=4)),
        ("12h", timedelta(hours=12)),
    ):
        counts[f"users_{key}"] = await session.scalar(
            select(func.count(User.id)).where(User.created_at >= now - delta)
        ) or 0

    counts["schools_total"] = await session.scalar(select(func.count(School.id))) or 0
    counts["schools_active"] = await session.scalar(
        select(func.count(School.id)).where(School.is_active.is_(True))
    ) or 0
    counts["classes_total"] = await session.scalar(select(func.count(ClassGroup.id))) or 0
    counts["classes_active"] = await session.scalar(
        select(func.count(ClassGroup.id)).where(ClassGroup.is_active.is_(True))
    ) or 0
    counts["groups_connected"] = await session.scalar(
        select(func.count(ClassGroup.id)).where(ClassGroup.group_chat_id.is_not(None))
    ) or 0
    counts["groups_missing"] = await session.scalar(
        select(func.count(ClassGroup.id)).where(ClassGroup.group_chat_id.is_(None))
    ) or 0
    counts["subjects"] = await session.scalar(select(func.count(Subject.id))) or 0
    counts["memberships_active"] = await session.scalar(
        select(func.count(ClassMembership.id)).where(ClassMembership.is_active.is_(True))
    ) or 0
    counts["homeworks"] = await session.scalar(select(func.count(Homework.id))) or 0
    counts["exams"] = await session.scalar(select(func.count(Exam.id))) or 0
    counts["reports"] = await session.scalar(select(func.count(DailyReport.id))) or 0
    counts["attendance"] = await session.scalar(select(func.count(Attendance.id))) or 0
    counts["grades"] = await session.scalar(select(func.count(Grade.id))) or 0
    counts["polls"] = await session.scalar(select(func.count(Poll.id))) or 0 if False else 0
    counts["files"] = await session.scalar(
        select(func.count(HomeworkSubmission.id)).where(HomeworkSubmission.file_id.is_not(None))
    ) or 0
    counts["pending_homework"] = await session.scalar(
        select(func.count(Homework.id)).where(Homework.status == RecordStatus.PENDING)
    ) or 0
    counts["pending_exams"] = await session.scalar(
        select(func.count(Exam.id)).where(Exam.status == RecordStatus.PENDING)
    ) or 0
    counts["pending_reports"] = await session.scalar(
        select(func.count(DailyReport.id)).where(DailyReport.status == RecordStatus.PENDING)
    ) or 0
    counts["outbox_pending"] = await session.scalar(
        select(func.count(TelegramOutbox.id)).where(
            TelegramOutbox.status.in_([OutboxStatus.PENDING, OutboxStatus.RETRY])
        )
    ) or 0
    counts["outbox_failed"] = await session.scalar(
        select(func.count(TelegramOutbox.id)).where(TelegramOutbox.status == OutboxStatus.FAILED)
    ) or 0
    started = perf_counter()
    await session.execute(text("SELECT 1"))
    counts["database_ping_ms"] = round((perf_counter() - started) * 1000, 1)
    try:
        counts["server_load"] = tuple(round(x, 2) for x in __import__("os").getloadavg())
    except (AttributeError, OSError):
        counts["server_load"] = None
    return counts


async def dashboard_preference(session: AsyncSession, telegram_id: int) -> AdminDashboardPreference:
    item = await session.scalar(
        select(AdminDashboardPreference).where(AdminDashboardPreference.telegram_id == telegram_id)
    )
    if item is None:
        item = AdminDashboardPreference(
            telegram_id=telegram_id,
            visible_cards=list(DEFAULT_DASHBOARD_CARDS),
            compact_lists=True,
        )
        session.add(item)
        await session.flush()
    return item


async def list_schools(session: AsyncSession, *, include_inactive: bool = True) -> list[School]:
    query = select(School).order_by(School.is_active.desc(), School.name, School.id)
    if not include_inactive:
        query = query.where(School.is_active.is_(True))
    return list((await session.scalars(query.limit(100))).all())


async def create_school(
    session: AsyncSession, *, actor: int, name: str, timezone_name: str = "Asia/Tehran"
) -> School:
    clean = " ".join(name.split())
    if not 2 <= len(clean) <= 180:
        raise ValueError("نام مدرسه باید بین ۲ تا ۱۸۰ نویسه باشد.")
    existing = await session.scalar(
        select(School.id).where(func.lower(School.name) == clean.casefold())
    )
    if existing is not None:
        raise ValueError("مدرسه‌ای با این نام قبلاً ثبت شده است.")
    school = School(name=clean, timezone=timezone_name, is_active=True)
    session.add(school)
    await session.flush()
    await audit_action(
        session, actor_telegram_id=actor, action="school.create",
        target_type="school", target_id=school.id, after={"name": school.name, "timezone": timezone_name},
    )
    return school


async def rename_school(session: AsyncSession, *, actor: int, school_id: int, name: str) -> School:
    school = await session.get(School, school_id, with_for_update=True)
    if school is None:
        raise ValueError("مدرسه پیدا نشد.")
    clean = " ".join(name.split())
    if not 2 <= len(clean) <= 180:
        raise ValueError("نام مدرسه باید بین ۲ تا ۱۸۰ نویسه باشد.")
    before = {"name": school.name}
    school.name = clean
    await audit_action(
        session, actor_telegram_id=actor, action="school.rename",
        target_type="school", target_id=school.id, before=before, after={"name": clean},
    )
    return school


async def set_school_active(
    session: AsyncSession, *, actor: int, school_id: int, active: bool
) -> School:
    school = await session.get(School, school_id, with_for_update=True)
    if school is None:
        raise ValueError("مدرسه پیدا نشد.")
    before = {"is_active": school.is_active}
    school.is_active = active
    await audit_action(
        session, actor_telegram_id=actor,
        action="school.activate" if active else "school.suspend",
        target_type="school", target_id=school.id, before=before, after={"is_active": active},
    )
    return school


async def list_classes(
    session: AsyncSession, *, school_id: int | None = None, include_inactive: bool = True
) -> list[tuple[ClassGroup, School]]:
    query = select(ClassGroup, School).join(School, ClassGroup.school_id == School.id)
    if school_id is not None:
        query = query.where(ClassGroup.school_id == school_id)
    if not include_inactive:
        query = query.where(ClassGroup.is_active.is_(True), School.is_active.is_(True))
    query = query.order_by(School.name, ClassGroup.name, ClassGroup.id)
    return list((await session.execute(query.limit(200))).all())


async def create_class(
    session: AsyncSession,
    *,
    actor: int,
    school_id: int,
    name: str,
    grade: str,
    section: str | None = None,
) -> tuple[ClassGroup, str]:
    school = await session.get(School, school_id, with_for_update=True)
    if school is None or not school.is_active:
        raise ValueError("مدرسه فعال پیدا نشد.")
    clean_name = " ".join(name.split())
    clean_grade = " ".join(grade.split())
    clean_section = " ".join((section or "").split()) or None
    if not 2 <= len(clean_name) <= 80:
        raise ValueError("نام کلاس باید بین ۲ تا ۸۰ نویسه باشد.")
    if not 1 <= len(clean_grade) <= 32:
        raise ValueError("پایه تحصیلی معتبر نیست.")
    cls = ClassGroup(
        school_id=school_id,
        name=clean_name,
        grade=clean_grade,
        section=clean_section,
        is_active=False,
    )
    session.add(cls)
    await session.flush()
    raw_code = make_code(8)
    cls.group_link_code_hash = hash_code(raw_code)
    cls.group_link_code_expires_at = code_expiry(30)
    admin_user = await session.scalar(select(User).where(User.telegram_id == actor))
    if admin_user is not None:
        membership = ClassMembership(
            class_id=cls.id,
            user_id=admin_user.id,
            role=Role.CLASS_MANAGER,
            is_active=True,
        )
        session.add(membership)
    await audit_action(
        session, actor_telegram_id=actor, action="class.create",
        target_type="class", target_id=cls.id,
        after={"school_id": school_id, "name": clean_name, "grade": clean_grade, "section": clean_section, "active": False, "group_required": True},
    )
    return cls, raw_code


async def set_class_active(
    session: AsyncSession, *, actor: int, class_id: int, active: bool
) -> ClassGroup:
    cls = await session.get(ClassGroup, class_id, with_for_update=True)
    if cls is None:
        raise ValueError("کلاس پیدا نشد.")
    if active and cls.group_chat_id is None:
        raise ValueError("تا زمانی که گروه تلگرام کلاس متصل نشده، کلاس قابل فعال‌سازی نیست.")
    before = {"is_active": cls.is_active}
    cls.is_active = active
    await audit_action(
        session, actor_telegram_id=actor, action="class.activate" if active else "class.suspend",
        target_type="class", target_id=class_id, before=before, after={"is_active": active},
    )
    return cls


async def issue_group_link_code(
    session: AsyncSession, *, actor: int, class_id: int
) -> tuple[ClassGroup, str]:
    cls = await session.get(ClassGroup, class_id, with_for_update=True)
    if cls is None:
        raise ValueError("کلاس پیدا نشد.")
    raw_code = make_code(8)
    before = {"group_chat_id": cls.group_chat_id, "group_chat_title": cls.group_chat_title}
    cls.group_link_code_hash = hash_code(raw_code)
    cls.group_link_code_expires_at = code_expiry(30)
    await audit_action(
        session, actor_telegram_id=actor, action="class.group_link_code.issue",
        target_type="class", target_id=class_id, before=before, after={"expires_in_minutes": 30},
    )
    return cls, raw_code


async def disconnect_group(
    session: AsyncSession, *, actor: int, class_id: int
) -> ClassGroup:
    cls = await session.get(ClassGroup, class_id, with_for_update=True)
    if cls is None:
        raise ValueError("کلاس پیدا نشد.")
    before = {"group_chat_id": cls.group_chat_id, "group_chat_title": cls.group_chat_title, "active": cls.is_active}
    cls.group_chat_id = None
    cls.group_chat_title = None
    cls.group_link_code_hash = None
    cls.group_link_code_expires_at = None
    cls.is_active = False
    await audit_action(
        session, actor_telegram_id=actor, action="class.group_disconnect",
        target_type="class", target_id=class_id, before=before,
        after={"group_chat_id": None, "is_active": False},
    )
    return cls


async def list_class_members(
    session: AsyncSession, class_id: int, *, active_only: bool = False
) -> list[tuple[ClassMembership, User]]:
    query = (
        select(ClassMembership, User)
        .join(User, ClassMembership.user_id == User.id)
        .where(ClassMembership.class_id == class_id)
        .order_by(ClassMembership.is_active.desc(), User.full_name, User.id)
    )
    if active_only:
        query = query.where(ClassMembership.is_active.is_(True))
    return list((await session.execute(query.limit(300))).all())


async def set_class_member_active(
    session: AsyncSession, *, actor: int, class_id: int, user_id: int, active: bool
) -> ClassMembership:
    membership = await session.scalar(
        select(ClassMembership)
        .where(ClassMembership.class_id == class_id, ClassMembership.user_id == user_id)
        .with_for_update()
    )
    if membership is None:
        raise ValueError("عضویت پیدا نشد.")
    before = {"is_active": membership.is_active, "role": membership.role.value}
    membership.is_active = active
    if active and membership.role != Role.STUDENT:
        # Reactivation from the admin panel never restores stale management privileges.
        membership.role = Role.STUDENT
    await audit_action(
        session, actor_telegram_id=actor,
        action="class.member.activate" if active else "class.member.suspend",
        target_type="class_membership", target_id=membership.id,
        before=before, after={"is_active": membership.is_active, "role": membership.role.value},
    )
    return membership


async def list_school_subjects(session: AsyncSession, school_id: int) -> list[Subject]:
    return list(
        (await session.scalars(select(Subject).where(Subject.school_id == school_id).order_by(Subject.name))).all()
    )


async def create_school_subject(
    session: AsyncSession, *, actor: int, school_id: int, name: str
) -> Subject:
    clean = " ".join(name.split())
    normalized = clean.replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ").casefold()
    if not clean or len(clean) > 100:
        raise ValueError("نام درس معتبر نیست.")
    existing = await session.scalar(
        select(Subject).where(Subject.school_id == school_id, Subject.normalized_name == normalized)
    )
    if existing:
        return existing
    subject = Subject(school_id=school_id, name=clean, normalized_name=normalized)
    session.add(subject)
    await session.flush()
    await audit_action(
        session, actor_telegram_id=actor, action="subject.create",
        target_type="subject", target_id=subject.id, after={"school_id": school_id, "name": clean},
    )
    return subject


async def assign_subject(
    session: AsyncSession, *, actor: int, class_id: int, subject_id: int
) -> ClassSubject:
    cls = await session.get(ClassGroup, class_id)
    subject = await session.get(Subject, subject_id)
    if cls is None or subject is None or cls.school_id != subject.school_id:
        raise ValueError("درس باید متعلق به همان مدرسه کلاس باشد.")
    link = await session.scalar(
        select(ClassSubject).where(ClassSubject.class_id == class_id, ClassSubject.subject_id == subject_id)
    )
    if link is None:
        link = ClassSubject(class_id=class_id, subject_id=subject_id)
        session.add(link)
        await session.flush()
        await audit_action(
            session, actor_telegram_id=actor, action="class.subject.assign",
            target_type="class_subject", target_id=link.id,
            after={"class_id": class_id, "subject_id": subject_id},
        )
    return link


async def unassign_subject(
    session: AsyncSession, *, actor: int, class_id: int, subject_id: int
) -> bool:
    link = await session.scalar(
        select(ClassSubject)
        .where(ClassSubject.class_id == class_id, ClassSubject.subject_id == subject_id)
        .with_for_update()
    )
    if link is None:
        return False
    await audit_action(
        session, actor_telegram_id=actor, action="class.subject.unassign",
        target_type="class_subject", target_id=link.id,
        before={"class_id": class_id, "subject_id": subject_id},
    )
    await session.delete(link)
    return True


async def list_admin_accounts(session: AsyncSession) -> list[AdminAccount]:
    return list((await session.scalars(select(AdminAccount).order_by(AdminAccount.is_active.desc(), AdminAccount.display_name))).all())


async def create_admin_account(
    session: AsyncSession, *, actor: int, telegram_id: int, display_name: str, role: str
) -> AdminAccount:
    if role not in {"operator", "reviewer", "analyst", "viewer"}:
        raise ValueError("نقش مدیر معتبر نیست.")
    existing = await session.scalar(select(AdminAccount).where(AdminAccount.telegram_id == telegram_id))
    if existing:
        existing.display_name = display_name.strip()[:160]
        existing.role = role
        existing.is_active = True
        account = existing
    else:
        account = AdminAccount(
            telegram_id=telegram_id,
            display_name=display_name.strip()[:160],
            role=role,
            created_by_telegram_id=actor,
        )
        session.add(account)
    await audit_action(
        session, actor_telegram_id=actor, action="admin.create_or_update",
        target_type="admin_account", target_id=telegram_id,
        after={"display_name": display_name, "role": role, "is_active": True},
    )
    return account


async def deactivate_admin_account(
    session: AsyncSession, *, actor: int, telegram_id: int
) -> bool:
    account = await session.scalar(
        select(AdminAccount).where(AdminAccount.telegram_id == telegram_id).with_for_update()
    )
    if account is None:
        return False
    if account.telegram_id == actor:
        raise ValueError("نمی‌توانید حساب مدیریتی خودتان را غیرفعال کنید.")
    account.is_active = False
    await audit_action(
        session, actor_telegram_id=actor, action="admin.deactivate",
        target_type="admin_account", target_id=telegram_id,
        before={"is_active": True}, after={"is_active": False},
    )
    return True


async def list_recent_audit(session: AsyncSession, limit: int = 20) -> list[AdminAuditEvent]:
    return list(
        (await session.scalars(
            select(AdminAuditEvent).order_by(AdminAuditEvent.created_at.desc()).limit(min(max(limit, 1), 100))
        )).all()
    )


async def list_class_content_queue(session: AsyncSession, limit: int = 30) -> list[tuple[str, Any, ClassGroup]]:
    pending: list[tuple[str, Any, ClassGroup]] = []
    for label, model in (("homework", Homework), ("exam", Exam), ("report", DailyReport)):
        rows = (
            await session.execute(
                select(model, ClassGroup)
                .join(ClassGroup, model.class_id == ClassGroup.id)
                .where(model.status == RecordStatus.PENDING)
                .order_by(model.created_at.asc())
                .limit(limit)
            )
        ).all()
        pending.extend((label, item, cls) for item, cls in rows)
    pending.sort(key=lambda item: item[1].created_at)
    return pending[:limit]
