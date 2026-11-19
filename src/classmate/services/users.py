from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))

    if user is None:
        user = User(
            telegram_id=telegram_id,
            username=username,
            full_name=full_name[:160],
            private_chat_started=private_chat_started,
        )
        session.add(user)
        await session.flush()
        return user

    # Keep profile fresh
    if username and user.username != username:
        user.username = username
    if full_name and user.full_name != full_name:
        user.full_name = full_name[:160]
    if private_chat_started and not user.private_chat_started:
        user.private_chat_started = True

    return user
