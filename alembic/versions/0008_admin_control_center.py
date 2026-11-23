"""Create shared management-bot access, audit and dashboard preferences.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False, server_default="operator"),
        sa.Column("permissions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_admin_accounts_telegram_id", "admin_accounts", ["telegram_id"], unique=True)
    op.create_table(
        "admin_audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=80), nullable=True),
        sa.Column("before_data", sa.JSON(), nullable=True),
        sa.Column("after_data", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_admin_audit_created", "admin_audit_events", ["created_at"], unique=False)
    op.create_index(
        "ix_admin_audit_actor_created", "admin_audit_events",
        ["actor_telegram_id", "created_at"], unique=False,
    )
    op.create_table(
        "admin_dashboard_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("visible_cards", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("compact_lists", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_admin_dashboard_preferences_telegram_id",
        "admin_dashboard_preferences", ["telegram_id"], unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_admin_dashboard_preferences_telegram_id", table_name="admin_dashboard_preferences")
    op.drop_table("admin_dashboard_preferences")
    op.drop_index("ix_admin_audit_actor_created", table_name="admin_audit_events")
    op.drop_index("ix_admin_audit_created", table_name="admin_audit_events")
    op.drop_table("admin_audit_events")
    op.drop_index("ix_admin_accounts_telegram_id", table_name="admin_accounts")
    op.drop_table("admin_accounts")
