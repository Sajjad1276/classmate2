"""Reminder delivery worker with leasing."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from sqlalchemy import select, update

from classmate.core.config import get_settings
from classmate.core.enums import ReminderStatus
from classmate.core.logging import configure_logging
from classmate.db.models import ReminderJob
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


async def process_once(bot: Bot) -> int:
    sent = 0
    async with SessionFactory() as session:
        async with session.begin():
            jobs = await claim_jobs(session)

        for job in jobs:
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
                            if row.attempts >= 5:
                                row.status = ReminderStatus.FAILED
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
