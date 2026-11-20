"""Reminder delivery worker with leasing."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNotFound
from sqlalchemy import select, update

from classmate.core.config import get_settings
from classmate.core.enums import EntityType, RecordStatus, ReminderStatus
from classmate.core.logging import configure_logging
from classmate.db.models import ClassGroup, ClassMembership, Exam, Homework, ReminderJob, User
from classmate.db.session import SessionFactory

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
            ReminderJob.locked_at < lease_expired,
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
    class_group = await session.get(ClassGroup, job.class_id)
    if class_group is None or not class_group.is_active:
        return False

    model = {
        EntityType.HOMEWORK.value: Homework,
        EntityType.EXAM.value: Exam,
    }.get(job.entity_type)
    if model is None:
        return False
    record = await session.get(model, job.entity_id)
    if (
        record is None
        or record.class_id != job.class_id
        or record.status != RecordStatus.APPROVED
    ):
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
                await bot.send_message(job.target_id, job.message_text)
                async with SessionFactory() as s2:
                    async with s2.begin():
                        row = await s2.get(ReminderJob, job.id)
                        if row:
                            row.status = ReminderStatus.SENT
                            row.sent_at = datetime.now(timezone.utc)
                            row.locked_at = None
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
                                    if recipient is not None:
                                        recipient.private_chat_started = False
                            else:
                                row.status = ReminderStatus.RETRY
                                row.scheduled_at = datetime.now(timezone.utc) + timedelta(
                                    minutes=2 ** row.attempts
                                )
    return sent


async def main() -> None:
    configure_logging()
    settings = get_settings()
    token = settings.bot_token.strip()
    if not token or token.startswith("123456"):
        raise RuntimeError("BOT_TOKEN not configured for worker")

    bot = Bot(token=token)
    logger.info("ClassMate 2 reminder worker started")
    try:
        while True:
            try:
                n = await process_once(bot)
                if n:
                    logger.info("Delivered %s reminders", n)
            except Exception:
                logger.exception("Worker loop error")
            await asyncio.sleep(15)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
