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
    # Bootstrap via metadata create for MVP speed.
    # Future changes must be explicit Alembic operations.
    from classmate.db.base import Base
    from classmate.db import models  # noqa: F401

    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    from classmate.db.base import Base
    from classmate.db import models  # noqa: F401

    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
