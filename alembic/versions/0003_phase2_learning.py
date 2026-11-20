"""add learning workflow tables for phase two

Revision ID: 0003
Revises: 0002
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "homework_submissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("homework_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text()), sa.Column("file_id", sa.String(255)),
        sa.Column("file_type", sa.String(32)), sa.Column("status", sa.String(24), nullable=False, server_default="submitted"),
        sa.Column("feedback", sa.Text()), sa.Column("reviewed_by", sa.Integer()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["homework_id"], ["homeworks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"]),
        sa.UniqueConstraint("homework_id", "student_id", name="uq_homework_student_submission"),
    )
    op.create_index("ix_submission_student_status", "homework_submissions", ["student_id", "status"])
    op.create_index("ix_homework_submissions_homework_id", "homework_submissions", ["homework_id"])
    op.create_index("ix_homework_submissions_student_id", "homework_submissions", ["student_id"])

    op.create_table(
        "grades",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False), sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(24), nullable=False), sa.Column("source_id", sa.Integer()),
        sa.Column("score", sa.Float(), nullable=False), sa.Column("max_score", sa.Float(), nullable=False, server_default="20"),
        sa.Column("feedback", sa.Text()), sa.Column("graded_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"]), sa.ForeignKeyConstraint(["graded_by"], ["users.id"]),
    )
    op.create_index("ix_grades_class_id", "grades", ["class_id"])
    op.create_index("ix_grades_student_id", "grades", ["student_id"])
    op.create_index("ix_grades_subject_id", "grades", ["subject_id"])
    op.create_index("ix_grade_student_created", "grades", ["student_id", "created_at"])
    op.create_index("ix_grade_class_subject", "grades", ["class_id", "subject_id"])

    op.create_table(
        "attendance",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False), sa.Column("attendance_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False), sa.Column("note", sa.Text()), sa.Column("marked_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["marked_by"], ["users.id"]),
        sa.UniqueConstraint("class_id", "student_id", "attendance_date", name="uq_attendance_student_day"),
    )
    op.create_index("ix_attendance_class_id", "attendance", ["class_id"])
    op.create_index("ix_attendance_student_id", "attendance", ["student_id"])
    op.create_index("ix_attendance_class_date", "attendance", ["class_id", "attendance_date"])

    op.create_table(
        "polls",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False), sa.Column("kind", sa.String(16), nullable=False, server_default="poll"),
        sa.Column("created_by", sa.Integer(), nullable=False), sa.Column("closes_at", sa.DateTime(timezone=True)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
    )
    op.create_index("ix_polls_class_id", "polls", ["class_id"]); op.create_index("ix_poll_class_active", "polls", ["class_id", "is_active"])
    op.create_table("poll_options", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("poll_id", sa.Integer(), nullable=False), sa.Column("label", sa.String(180), nullable=False), sa.Column("position", sa.Integer(), nullable=False), sa.ForeignKeyConstraint(["poll_id"], ["polls.id"], ondelete="CASCADE"))
    op.create_index("ix_poll_options_poll_id", "poll_options", ["poll_id"])
    op.create_table("poll_responses", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("poll_id", sa.Integer(), nullable=False), sa.Column("option_id", sa.Integer()), sa.Column("user_id", sa.Integer(), nullable=False), sa.Column("answer_text", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.ForeignKeyConstraint(["poll_id"], ["polls.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["option_id"], ["poll_options.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"), sa.UniqueConstraint("poll_id", "user_id", name="uq_poll_user_response"))
    op.create_index("ix_poll_responses_poll_id", "poll_responses", ["poll_id"]); op.create_index("ix_poll_responses_user_id", "poll_responses", ["user_id"])


def downgrade() -> None:
    op.drop_table("poll_responses"); op.drop_table("poll_options"); op.drop_table("polls"); op.drop_table("attendance"); op.drop_table("grades"); op.drop_table("homework_submissions")
