"""Historical compatibility revision; the frozen 0001 baseline already includes announcements."""

from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Kept as a no-op for databases that already ran the original bootstrap.
    pass


def downgrade() -> None:
    # Never drop application data during a migration rollback.
    pass
