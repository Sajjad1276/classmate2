"""Replace academic-year indirection with direct school ownership.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    class_columns = {column["name"] for column in inspector.get_columns("classes")}
    if "school_id" not in class_columns:
        op.add_column("classes", sa.Column("school_id", sa.Integer(), nullable=True))

    op.execute(
        """
        UPDATE classes AS c
        SET school_id = ay.school_id
        FROM academic_years AS ay
        WHERE c.academic_year_id = ay.id
          AND c.school_id IS NULL
        """
    )
    orphan_count = bind.execute(
        sa.text("SELECT count(*) FROM classes WHERE school_id IS NULL")
    ).scalar_one()
    if orphan_count:
        raise RuntimeError(
            f"Cannot remove academic years: {orphan_count} class rows have no school owner."
        )

    current_fks = sa.inspect(bind).get_foreign_keys("classes")
    if not any(fk.get("constrained_columns") == ["school_id"] for fk in current_fks):
        op.create_foreign_key(
            "fk_classes_school_id_schools", "classes", "schools",
            ["school_id"], ["id"], ondelete="CASCADE",
        )
    op.alter_column("classes", "school_id", existing_type=sa.Integer(), nullable=False)
    if not any(
        idx.get("name") == "ix_classes_school_id"
        for idx in sa.inspect(bind).get_indexes("classes")
    ):
        op.create_index("ix_classes_school_id", "classes", ["school_id"], unique=False)

    inspector = sa.inspect(bind)
    for fk in inspector.get_foreign_keys("classes"):
        if fk.get("constrained_columns") == ["academic_year_id"] and fk.get("name"):
            op.drop_constraint(fk["name"], "classes", type_="foreignkey")
    for idx in inspector.get_indexes("classes"):
        if idx.get("column_names") == ["academic_year_id"] and idx.get("name"):
            op.drop_index(idx["name"], table_name="classes")
    if "academic_year_id" in class_columns:
        op.drop_column("classes", "academic_year_id")
    if sa.inspect(bind).has_table("academic_years"):
        op.drop_table("academic_years")


def downgrade() -> None:
    raise RuntimeError(
        "This migration is intentionally irreversible; restore a database backup to reintroduce academic years."
    )
