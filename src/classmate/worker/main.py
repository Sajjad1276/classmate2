"""Reminder delivery worker with leasing."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNotFound, TelegramRetryAfter
from sqlalchemy import select, update

from classmate.core.config import get_settings
from classmate.core.enums import EntityType, OutboxStatus, RecordStatus, ReminderStatus
from classmate.core.logging import configure_logging
from classmate.db.models import AcademicYear, Announcement, ClassGroup, ClassMembership, Exam, DailyReport, Homework, ReminderJob, School, TelegramOutbox, User
from classmate.db.session import SessionFactory
from classmate.services.group_invites import schedule_group_invite_reminders
from classmate.services.permissions import hash_code
from classmate.services.class_plans import seed_class104_plan

logger = logging.getLogger(__name__)
LEASE_SECONDS = 60
BATCH = 20


async def claim_jobs(session):
    now = datetime.now(timezone.utc)
    lease_expired = now - timedelta(seconds=LEASE_SECONDS)

    # Recover expired leases
    await session.execute(
        update(ReminderJob)
        .where(
            ReminderJob.status == ReminderStatus.PROCESSING,
            (ReminderJob.locked_at.is_(None))
            | (ReminderJob.locked_at < lease_expired),
        )
        .values(status=ReminderStatus.PENDING, locked_at=None)
    )

    rows = (
        await session.execute(
            select(ReminderJob)
            .where(
                ReminderJob.status.in_([ReminderStatus.PENDING, ReminderStatus.RETRY]),
                ReminderJob.scheduled_at <= now,
            )
            .order_by(ReminderJob.scheduled_at)
            .limit(BATCH)
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()

    for job in rows:
        job.status = ReminderStatus.PROCESSING
        job.locked_at = now
    await session.flush()
    return rows


async def _job_is_deliverable(session, job: ReminderJob) -> bool:
    """Revalidate the record, class and recipient immediately before delivery."""
    class_group = await session.scalar(
        select(ClassGroup)
        .join(AcademicYear, ClassGroup.academic_year_id == AcademicYear.id)
        .join(School, AcademicYear.school_id == School.id)
        .where(
            ClassGroup.id == job.class_id,
            ClassGroup.is_active.is_(True),
            School.is_active.is_(True),
        )
    )
    if class_group is None:
        return False

    if job.entity_type == "class_invite":
        if job.target_type != "group" or class_group.group_chat_id != job.target_id:
            return False
        if not job.button_text or not job.button_url:
            return False
        marker = "?start=join_"
        if marker not in job.button_url:
            return False
        token = job.button_url.split(marker, 1)[1]
        if not token or token != class_group.member_join_code_token:
            return False
        if class_group.member_join_code_hash != hash_code(token):
            return False
        expires_at = class_group.member_join_code_expires_at
        if expires_at is not None:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= datetime.now(timezone.utc):
                return False
        return True

    model = {
        EntityType.HOMEWORK.value: Homework,
        EntityType.EXAM.value: Exam,
        EntityType.ANNOUNCEMENT.value: Announcement,
    }.get(job.entity_type)
    if model is None:
        return False
    record = await session.get(model, job.entity_id)
    if record is None or record.class_id != job.class_id:
        return False
    if job.entity_type == EntityType.ANNOUNCEMENT.value:
        if not record.is_active:
            return False
        expires_at = record.expires_at
        if expires_at is not None:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= datetime.now(timezone.utc):
                return False
    elif record.status != RecordStatus.APPROVED:
        return False

    if job.target_type == "group":
        return class_group.group_chat_id == job.target_id

    if job.target_type == "private":
        user = await session.scalar(
            select(User).where(
                User.telegram_id == job.target_id,
                User.is_active.is_(True),
                User.private_chat_started.is_(True),
            )
        )
        if user is None:
            return False
        membership_id = await session.scalar(
            select(ClassMembership.id).where(
                ClassMembership.class_id == job.class_id,
                ClassMembership.user_id == user.id,
                ClassMembership.is_active.is_(True),
            )
        )
        return membership_id is not None
    return False


async def process_once(bot: Bot) -> int:
    sent = 0
    async with SessionFactory() as session:
        async with session.begin():
            jobs = await claim_jobs(session)

        for job in jobs:
            # Permissions and record state may have changed while the job waited.
            async with SessionFactory() as validation_session:
                async with validation_session.begin():
                    deliverable = await _job_is_deliverable(validation_session, job)
                    if not deliverable:
                        row = await validation_session.get(ReminderJob, job.id)
                        if row and row.status == ReminderStatus.PROCESSING:
                            row.status = ReminderStatus.CANCELLED
                            row.locked_at = None
            if not deliverable:
                continue
            try:
                reply_markup = None
                if job.button_text and job.button_url:
                    reply_markup = InlineKeyboardMarkup(
                        inline_keyboard=[[
                            InlineKeyboardButton(text=job.button_text, url=job.button_url)
                        ]]
                    )
                await bot.send_message(
                    chat_id=job.target_id,
                    text=job.message_text,
                    reply_markup=reply_markup,
                    parse_mode="HTML" if reply_markup is not None else None,
                )
                async with SessionFactory() as s2:
                    async with s2.begin():
                        row = await s2.get(ReminderJob, job.id)
                        if row:
                            row.status = ReminderStatus.SENT
                            row.sent_at = datetime.now(timezone.utc)
                            row.locked_at = None
                            if row.entity_type == "class_invite":
                                linked_class = await s2.get(ClassGroup, row.class_id)
                                if linked_class is not None:
                                    linked_class.member_invite_reminder_last_sent_at = row.sent_at
                sent += 1
            except Exception as exc:
                logger.exception("Failed to send reminder %s", job.id)
                async with SessionFactory() as s2:
                    async with s2.begin():
                        row = await s2.get(ReminderJob, job.id)
                        if row:
                            row.attempts += 1
                            row.last_error = str(exc)[:500]
                            row.locked_at = None
                            permanent = isinstance(
                                exc, (TelegramBadRequest, TelegramForbiddenError, TelegramNotFound)
                            )
                            if permanent or row.attempts >= 5:
                                row.status = ReminderStatus.FAILED
                                if row.target_type == "private":
                                    recipient = await s2.scalar(
                                        select(User).where(User.telegram_id == row.target_id)
                                    )
                                    if (
                                        recipient is not None
                                        and isinstance(exc, (TelegramForbiddenError, TelegramNotFound))
                                    ):
                                        recipient.private_chat_started = False
                            else:
                                row.status = ReminderStatus.RETRY
                                row.scheduled_at = datetime.now(timezone.utc) + timedelta(
                                    minutes=2 ** row.attempts
                                )
    return sent


async def _outbox_is_deliverable(session, row: TelegramOutbox) -> bool:
    """Revalidate the business record and recipient immediately before Telegram I/O."""
    if row.entity_type is not None:
        model = {
            EntityType.HOMEWORK.value: Homework,
            EntityType.EXAM.value: Exam,
            EntityType.REPORT.value: DailyReport,
        }.get(row.entity_type)
        if model is None or row.entity_id is None:
            return False
        record = await session.get(model, row.entity_id)
        if (
            record is None
            or record.class_id != row.class_id
            or str(record.status) != row.expected_record_status
        ):
            return False

    if row.target_type == "group":
        result = await session.execute(
            select(ClassGroup.id)
            .join(AcademicYear, ClassGroup.academic_year_id == AcademicYear.id)
            .join(School, AcademicYear.school_id == School.id)
            .where(
                ClassGroup.id == row.class_id,
                ClassGroup.group_chat_id == row.target_chat_id,
                ClassGroup.is_active.is_(True),
                School.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none() is not None

    if row.target_type != "private" or row.recipient_user_id is None:
        return False

    user = await session.scalar(
        select(User).where(
            User.id == row.recipient_user_id,
            User.telegram_id == row.target_chat_id,
            User.is_active.is_(True),
            User.private_chat_started.is_(True),
        )
    )
    if user is None:
        return False
    if row.class_id is None:
        return True

    membership_id = await session.scalar(
        select(ClassMembership.id)
        .join(ClassGroup, ClassMembership.class_id == ClassGroup.id)
        .join(AcademicYear, ClassGroup.academic_year_id == AcademicYear.id)
        .join(School, AcademicYear.school_id == School.id)
        .where(
            ClassMembership.class_id == row.class_id,
            ClassMembership.user_id == user.id,
            ClassMembership.is_active.is_(True),
            ClassGroup.is_active.is_(True),
            School.is_active.is_(True),
        )
    )
    return membership_id is not None


async def _claim_outbox_ids(session) -> list[int]:
    now = datetime.now(timezone.utc)
    lease_expired = now - timedelta(seconds=LEASE_SECONDS)
    await session.execute(
        update(TelegramOutbox)
        .where(
            TelegramOutbox.status == OutboxStatus.PROCESSING,
            (TelegramOutbox.locked_at.is_(None))
            | (TelegramOutbox.locked_at < lease_expired),
        )
        .values(status=OutboxStatus.PENDING, locked_at=None)
    )
    ids = list(
        (
            await session.scalars(
                select(TelegramOutbox.id)
                .where(
                    TelegramOutbox.status.in_(
                        [OutboxStatus.PENDING, OutboxStatus.RETRY]
                    ),
                    TelegramOutbox.scheduled_at <= now,
                )
                .order_by(TelegramOutbox.scheduled_at, TelegramOutbox.id)
                .limit(BATCH)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    if ids:
        await session.execute(
            update(TelegramOutbox)
            .where(TelegramOutbox.id.in_(ids))
            .values(status=OutboxStatus.PROCESSING, locked_at=now)
        )
    return ids


async def process_outbox_once(bot: Bot) -> int:
    """Deliver committed outbox messages (at-least-once, never fire-and-forget)."""
    async with SessionFactory() as claim_session:
        async with claim_session.begin():
            job_ids = await _claim_outbox_ids(claim_session)

    delivered = 0
    for job_id in job_ids:
        payload = None
        async with SessionFactory() as validation_session:
            async with validation_session.begin():
                row = await validation_session.get(TelegramOutbox, job_id)
                if row is None or row.status != OutboxStatus.PROCESSING:
                    continue
                # Approval-button messages from the former group-based review flow
                # must never leak into class groups after the private review panel ships.
                if row.action_type is not None:
                    row.status = OutboxStatus.CANCELLED
                    row.locked_at = None
                    continue
                if not await _outbox_is_deliverable(validation_session, row):
                    row.status = OutboxStatus.CANCELLED
                    row.locked_at = None
                    continue
                payload = {
                    "target_chat_id": row.target_chat_id,
                    "text": row.text,
                    "parse_mode": row.parse_mode,
                    "disable_notification": row.disable_notification,
                    "action_type": row.action_type,
                    "action_id": row.action_id,
                }

        if payload is None:
            continue

        try:
            reply_markup = None
            reply_markup = None
            await bot.send_message(
                chat_id=payload["target_chat_id"],
                text=payload["text"],
                parse_mode=payload["parse_mode"],
                disable_notification=payload["disable_notification"],
                reply_markup=reply_markup,
            )
        except Exception as exc:
            logger.exception("Failed to deliver Telegram outbox message %s", job_id)
            async with SessionFactory() as retry_session:
                async with retry_session.begin():
                    row = await retry_session.get(TelegramOutbox, job_id)
                    if row is None or row.status != OutboxStatus.PROCESSING:
                        continue
                    row.attempts += 1
                    row.last_error = str(exc)[:500]
                    row.locked_at = None
                    permanent = isinstance(
                        exc,
                        (TelegramBadRequest, TelegramForbiddenError, TelegramNotFound),
                    )
                    if permanent or row.attempts >= 7:
                        row.status = OutboxStatus.FAILED
                        if (
                            isinstance(exc, TelegramForbiddenError)
                            and row.target_type == "private"
                            and row.recipient_user_id is not None
                        ):
                            recipient = await retry_session.get(
                                User, row.recipient_user_id
                            )
                            if recipient is not None:
                                recipient.private_chat_started = False
                    else:
                        row.status = OutboxStatus.RETRY
                        delay_seconds = (
                            exc.retry_after
                            if isinstance(exc, TelegramRetryAfter)
                            else min(3600, 2 ** row.attempts)
                        )
                        row.scheduled_at = datetime.now(timezone.utc) + timedelta(
                            seconds=delay_seconds
                        )
            continue

        async with SessionFactory() as done_session:
            async with done_session.begin():
                row = await done_session.get(TelegramOutbox, job_id)
                if row is not None and row.status == OutboxStatus.PROCESSING:
                    row.status = OutboxStatus.SENT
                    row.sent_at = datetime.now(timezone.utc)
                    row.locked_at = None
                    row.last_error = None
                    delivered += 1
    return delivered


async def main() -> None:
    configure_logging()
    settings = get_settings()
    token = settings.bot_token.strip()
    if not token or token.startswith("123456"):
        raise RuntimeError("BOT_TOKEN not configured for worker")

    bot = Bot(token=token)
    logger.info("ClassMate 2 reminder worker started")
    try:
        bot_profile = await bot.get_me()
        bot_username = bot_profile.username or ""
        try:
            async with SessionFactory() as session:
                async with session.begin():
                    result = await seed_class104_plan(session)
                    invite_result = await schedule_group_invite_reminders(
                        session, bot_username=bot_username
                    )
            if result:
                logger.info(
                    "Class 104 plan seeded: class_id=%s announcement_id=%s reminder_jobs=%s candidates=%s group_targets=%s private_targets=%s",
                    result["class_id"],
                    result["announcement_id"],
                    result["reminder_jobs"],
                    result.get("candidate_reminder_jobs", 0),
                    result.get("group_targets", 0),
                    result.get("private_targets", 0),
                )
            logger.info(
                "Group invite reminders initialized=%s queued=%s rotated=%s",
                invite_result["initialized"],
                invite_result["queued"],
                invite_result["rotated"],
            )
        except Exception:
            # Missing or ambiguous classes must not stop the standard worker.
            logger.exception("Could not initialize ClassMate reminder schedules")
        last_plan_seed_at = datetime.now(timezone.utc)
        while True:
            try:
                now = datetime.now(timezone.utc)
                if now - last_plan_seed_at >= timedelta(minutes=5):
                    try:
                        async with SessionFactory() as session:
                            async with session.begin():
                                result = await seed_class104_plan(session)
                                invite_result = await schedule_group_invite_reminders(
                                    session, bot_username=bot_username
                                )
                        if result:
                            logger.info(
                                "Class 104 plan refresh: class_id=%s reminder_jobs=%s candidates=%s group_targets=%s private_targets=%s",
                                result["class_id"],
                                result["reminder_jobs"],
                                result.get("candidate_reminder_jobs", 0),
                                result.get("group_targets", 0),
                                result.get("private_targets", 0),
                            )
                        if any(invite_result.values()):
                            logger.info(
                                "Group invite reminder refresh initialized=%s queued=%s rotated=%s",
                                invite_result["initialized"],
                                invite_result["queued"],
                                invite_result["rotated"],
                            )
                    except Exception:
                        logger.exception("Could not refresh ClassMate reminder schedules")
                    finally:
                        last_plan_seed_at = now
                reminders_sent = await process_once(bot)
                outbox_sent = await process_outbox_once(bot)
                if reminders_sent or outbox_sent:
                    logger.info(
                        "Delivered %s reminders and %s outbox messages",
                        reminders_sent,
                        outbox_sent,
                    )
            except Exception:
                logger.exception("Worker loop error")
            await asyncio.sleep(15)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
