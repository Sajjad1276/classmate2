from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.core.config import get_settings
from classmate.db.models import User


async def ensure_user(
    session: AsyncSession,
    *,
    telegram_id: int,
    username: str | None,
    full_name: str,
    private_chat_started: bool = False,
) -> User:
    """Get or create a user. Update profile fields when they change."""
    is_bootstrap_admin = telegram_id in get_settings().bootstrap_admin_id_set
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    now = datetime.now(UTC)

    if user is None:
        user = User(
            telegram_id=telegram_id,
            username=username,
            full_name=full_name[:160],
            is_platform_admin=is_bootstrap_admin,
            private_chat_started=private_chat_started,
            first_private_started_at=now if private_chat_started else None,
            last_seen_at=now if private_chat_started else None,
        )
        session.add(user)
        await session.flush()
        return user

    if not user.is_active:
        raise PermissionError("حساب کاربری شما غیرفعال شده است.")

    # Bootstrap admins are promoted from trusted deployment configuration only.
    if is_bootstrap_admin and not user.is_platform_admin:
        user.is_platform_admin = True

    # Keep profile fresh
    if username and user.username != username:
        user.username = username
    if full_name and user.full_name != full_name:
        user.full_name = full_name[:160]
    if private_chat_started:
        user.private_chat_started = True
        if user.first_private_started_at is None:
            user.first_private_started_at = now
        user.last_seen_at = now

    return user
