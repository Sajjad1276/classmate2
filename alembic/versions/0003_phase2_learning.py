"""Historical compatibility revision; the frozen 0001 baseline includes phase-two tables."""

from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Kept as a no-op for databases that already ran the original bootstrap.
    pass


def downgrade() -> None:
    # Never drop submissions, grades, attendance or poll data automatically.
    pass
