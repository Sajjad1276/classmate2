"""Transactional enqueue API for Telegram notifications."""

from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.core.enums import OutboxStatus, RecordStatus
from classmate.db.models import TelegramOutbox

TargetType = Literal["group", "private"]
_ALLOWED_PARSE_MODES = {"HTML", "MarkdownV2", None}
_ALLOWED_ACTIONS = {"homework", "exam", "report"}


async def enqueue_telegram_message(
    session: AsyncSession,
    *,
    target_type: TargetType,
    target_chat_id: int,
    text: str,
    idempotency_key: str,
    class_id: int | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    expected_record_status: str | None = None,
    recipient_user_id: int | None = None,
    parse_mode: str | None = "HTML",
    action_type: str | None = None,
    action_id: int | None = None,
    disable_notification: bool = False,
    scheduled_at: datetime | None = None,
) -> TelegramOutbox:
    """Insert an outbox message in the caller's business transaction.

    Repeated calls with the same idempotency key and same payload return the
    existing row. Reusing a key for a different payload is treated as a bug.
    """
    normalized_text = text.strip()
    if not normalized_text or len(normalized_text) > 4096:
        raise ValueError("Outbox message text must contain 1 to 4096 characters.")
    if not idempotency_key or len(idempotency_key) > 180:
        raise ValueError("Outbox idempotency key must contain 1 to 180 characters.")
    if target_chat_id == 0:
        raise ValueError("Telegram target chat id cannot be zero.")
    if parse_mode not in _ALLOWED_PARSE_MODES:
        raise ValueError("Unsupported Telegram parse mode.")
    if target_type == "group":
        if class_id is None or recipient_user_id is not None:
            raise ValueError("Group outbox messages require a class and no recipient user.")
    elif target_type == "private":
        if recipient_user_id is None:
            raise ValueError("Private outbox messages require a recipient user.")
        if action_type is not None or action_id is not None:
            raise ValueError("Private outbox messages cannot contain approval actions.")
    else:
        raise ValueError("Unsupported outbox target type.")
    if (action_type is None) != (action_id is None):
        raise ValueError("Outbox action type and id must be provided together.")
    if action_type is not None and action_type not in _ALLOWED_ACTIONS:
        raise ValueError("Unsupported outbox action type.")
    if action_type is not None and target_type != "group":
        raise ValueError("Approval actions are only supported in class groups.")
    entity_fields = (entity_type, entity_id, expected_record_status)
    if any(value is not None for value in entity_fields) and not all(
        value is not None for value in entity_fields
    ):
        raise ValueError("Outbox record scope requires entity type, id and expected status.")
    if entity_type is not None and entity_type not in {"homework", "exam", "report"}:
        raise ValueError("Unsupported outbox record entity type.")
    allowed_statuses = {status.value for status in RecordStatus}
    if expected_record_status is not None and expected_record_status not in allowed_statuses:
        raise ValueError("Unsupported expected record status.")
    if scheduled_at is not None and scheduled_at.tzinfo is None:
        raise ValueError("Outbox scheduled_at must be timezone-aware.")

    when = scheduled_at or datetime.now(timezone.utc)
    values = {
        "target_type": target_type,
        "target_chat_id": target_chat_id,
        "class_id": class_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "expected_record_status": expected_record_status,
        "recipient_user_id": recipient_user_id,
        "text": normalized_text,
        "parse_mode": parse_mode,
        "disable_notification": disable_notification,
        "action_type": action_type,
        "action_id": action_id,
        "scheduled_at": when,
        "status": OutboxStatus.PENDING,
        "idempotency_key": idempotency_key,
    }
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        statement = pg_insert(TelegramOutbox).values(**values).on_conflict_do_nothing(
            index_elements=[TelegramOutbox.idempotency_key]
        )
        await session.execute(statement)
    elif dialect == "sqlite":
        statement = sqlite_insert(TelegramOutbox).values(**values).on_conflict_do_nothing(
            index_elements=[TelegramOutbox.idempotency_key]
        )
        await session.execute(statement)
    else:
        existing = await session.scalar(
            select(TelegramOutbox).where(
                TelegramOutbox.idempotency_key == idempotency_key
            )
        )
        if existing is None:
            session.add(TelegramOutbox(**values))
            await session.flush()

    existing = await session.scalar(
        select(TelegramOutbox).where(
            TelegramOutbox.idempotency_key == idempotency_key
        )
    )
    if existing is None:
        raise RuntimeError("Outbox insert did not produce a durable row.")

    comparable = (
        "target_type",
        "target_chat_id",
        "class_id",
        "entity_type",
        "entity_id",
        "expected_record_status",
        "recipient_user_id",
        "text",
        "parse_mode",
        "disable_notification",
        "action_type",
        "action_id",
    )
    if any(getattr(existing, field) != values[field] for field in comparable):
        raise ValueError("Outbox idempotency key was reused with a different payload.")
    return existing
