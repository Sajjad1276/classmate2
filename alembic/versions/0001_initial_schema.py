"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-11-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # This metadata is a frozen copy from the original schema. Do not import
    # the live ORM metadata here: future model changes require new revisions.
    from classmate.db.schema_v0001 import SnapshotBase

    SnapshotBase.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    # The initial revision is an irreversible schema baseline. Dropping all
    # metadata here could destroy tables/data created after 0001. Restore a
    # backup for rollback; this revision intentionally never drops objects.
    pass
