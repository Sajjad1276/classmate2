"""add announcements for phase one

Revision ID: 0002
Revises: 0001
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if not inspect(bind).has_table("announcements"):
        # Use the canonical model definition so columns, constraints and indexes
        # cannot silently drift from the ORM schema.
        from classmate.db.models import Announcement

        Announcement.__table__.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("announcements"):
        op.drop_table("announcements")
