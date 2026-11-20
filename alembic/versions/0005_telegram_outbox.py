"""Create durable transactional Telegram outbox."""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if not inspect(bind).has_table("telegram_outbox"):
        from classmate.db.models import TelegramOutbox

        TelegramOutbox.__table__.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    # Preserve queued notifications. Operational rollback should deploy the
    # previous application while retaining the outbox table for later replay.
    pass
