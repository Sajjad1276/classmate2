"""Ensure the announcements table exists for phase one."""

from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use the immutable initial snapshot for databases whose original 0001
    # revision ran before announcements existed in the application models.
    from classmate.db.schema_v0001 import Announcement

    Announcement.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    # Historical bootstrap may have created this table before 0002 was stamped.
    # Do not drop data automatically.
    pass
