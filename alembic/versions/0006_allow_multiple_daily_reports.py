"""allow multiple daily reports per class and date

Revision ID: 0006
Revises: 0005
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONSTRAINT_NAME = "uq_daily_report_class_date"
TABLE_NAME = "daily_reports"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(TABLE_NAME):
        return

    unique_constraints = inspector.get_unique_constraints(TABLE_NAME)
    if any(item.get("name") == CONSTRAINT_NAME for item in unique_constraints):
        # batch_alter_table uses SQLite's table-rebuild strategy where needed
        # and a normal ALTER TABLE on PostgreSQL.
        with op.batch_alter_table(TABLE_NAME) as batch_op:
            batch_op.drop_constraint(CONSTRAINT_NAME, type_="unique")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(TABLE_NAME):
        return

    duplicate = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM daily_reports
            GROUP BY class_id, report_date
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "Cannot restore uq_daily_report_class_date: multiple reports exist "
            "for at least one class and date. Consolidate them before downgrading."
        )

    unique_constraints = inspector.get_unique_constraints(TABLE_NAME)
    if not any(item.get("name") == CONSTRAINT_NAME for item in unique_constraints):
        with op.batch_alter_table(TABLE_NAME) as batch_op:
            batch_op.create_unique_constraint(
                CONSTRAINT_NAME, ["class_id", "report_date"]
            )
