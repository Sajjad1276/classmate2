"""Authorization for the separate management bot, backed by ClassMate 2 tables."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from classmate.core.config import Settings, get_settings
from classmate.db.models import AdminAccount

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "owner": {"*"},
    "operator": {
        "dashboard:read",
        "schools:read", "schools:write",
        "classes:read", "classes:write",
        "groups:read", "groups:write",
        "subjects:read", "subjects:write",
        "members:read", "members:write",
        "reports:read",
        "communications:read", "communications:write",
    },
    "reviewer": {
        "dashboard:read",
        "approvals:read", "approvals:write",
        "reports:read",
        "members:read",
    },
    "analyst": {
        "dashboard:read",
        "reports:read",
        "schools:read", "classes:read", "members:read",
        "groups:read", "subjects:read",
    },
    "viewer": {
        "dashboard:read",
        "schools:read", "classes:read", "members:read",
        "groups:read", "subjects:read", "reports:read",
    },
}

ROLE_LABELS = {
    "owner": "مالک سامانه",
    "operator": "اپراتور",
    "reviewer": "تأییدکننده",
    "analyst": "تحلیل‌گر",
    "viewer": "فقط مشاهده",
}


async def admin_account(session: AsyncSession, telegram_id: int) -> AdminAccount | None:
    return await session.scalar(
        select(AdminAccount).where(
            AdminAccount.telegram_id == telegram_id,
            AdminAccount.is_active.is_(True),
        )
    )


async def admin_permissions(
    session: AsyncSession,
    telegram_id: int,
    settings: Settings | None = None,
) -> set[str]:
    settings = settings or get_settings()
    if telegram_id in settings.bootstrap_admin_id_set:
        return {"*"}
    account = await admin_account(session, telegram_id)
    if account is None:
        return set()
    if account.permissions:
        return set(account.permissions)
    return set(ROLE_PERMISSIONS.get(account.role, set()))


async def has_admin_permission(
    session: AsyncSession,
    telegram_id: int,
    permission: str,
    settings: Settings | None = None,
) -> bool:
    permissions = await admin_permissions(session, telegram_id, settings)
    return "*" in permissions or permission in permissions


async def require_admin_permission(
    session: AsyncSession,
    telegram_id: int,
    permission: str,
    settings: Settings | None = None,
) -> None:
    if not await has_admin_permission(session, telegram_id, permission, settings):
        raise PermissionError("دسترسی لازم برای این عملیات را ندارید.")


async def is_bootstrap_owner(telegram_id: int, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return telegram_id in settings.bootstrap_admin_id_set
