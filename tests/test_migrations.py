"""Fresh database and repeatability smoke test for Alembic."""

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import Column, MetaData, String, Table, create_engine, inspect


def _run_alembic(root: Path, database_url: str, *args: str, env: dict[str, str]) -> None:
    command = [sys.executable, "-m", "alembic", *args]
    result = subprocess.run(
        command,
        cwd=root,
        env={**env, "DATABASE_URL": database_url},
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise AssertionError(
            f"Alembic {' '.join(args)} failed.\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )


def test_alembic_head_installs_without_bot_token_and_is_repeatable(tmp_path, monkeypatch):
    database_path = tmp_path / "fresh.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    root = Path(__file__).resolve().parents[1]

    env = os.environ.copy()
    env.pop("BOT_TOKEN", None)
    env.pop("PYTHONPATH", None)
    env["PYTHONPATH"] = str(root / "src")

    _run_alembic(root, database_url, "upgrade", "head", env=env)
    _run_alembic(root, database_url, "upgrade", "head", env=env)
    _run_alembic(root, database_url, "downgrade", "base", env=env)
    _run_alembic(root, database_url, "upgrade", "head", env=env)

    engine = create_engine(f"sqlite:///{database_path}")
    try:
        tables = set(inspect(engine).get_table_names())
        assert {
            "users",
            "announcements",
            "homework_submissions",
            "poll_responses",
            "reminder_jobs",
            "telegram_outbox",
            "class_member_invite_codes",
        }.issubset(tables)
        assert "alembic_version" in tables
        daily_report_constraints = inspect(engine).get_unique_constraints("daily_reports")
        assert all(
            item.get("name") != "uq_daily_report_class_date"
            for item in daily_report_constraints
        )
    finally:
        engine.dispose()



def test_initial_migration_snapshot_is_frozen():
    from classmate.db.models import Base
    from classmate.db.schema_v0001 import SnapshotBase

    assert "telegram_outbox" in Base.metadata.tables
    assert "telegram_outbox" not in SnapshotBase.metadata.tables



def test_legacy_0001_database_receives_missing_phase_tables(tmp_path):
    from classmate.db.schema_v0001 import SnapshotBase

    database_path = tmp_path / "legacy.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    root = Path(__file__).resolve().parents[1]
    missing_tables = {
        "announcements",
        "homework_submissions",
        "grades",
        "attendance",
        "polls",
        "poll_options",
        "poll_responses",
    }
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        existing = [
            table
            for name, table in SnapshotBase.metadata.tables.items()
            if name not in missing_tables
        ]
        SnapshotBase.metadata.create_all(engine, tables=existing)
        version_metadata = MetaData()
        version_table = Table(
            "alembic_version",
            version_metadata,
            Column("version_num", String(32), primary_key=True, nullable=False),
        )
        version_metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(version_table.insert().values(version_num="0001"))
    finally:
        engine.dispose()

    env = os.environ.copy()
    env.pop("BOT_TOKEN", None)
    env.pop("PYTHONPATH", None)
    env["PYTHONPATH"] = str(root / "src")
    _run_alembic(root, database_url, "upgrade", "head", env=env)

    engine = create_engine(f"sqlite:///{database_path}")
    try:
        tables = set(inspect(engine).get_table_names())
        assert missing_tables.issubset(tables)
        assert "telegram_outbox" in tables
        assert "class_member_invite_codes" in tables
        daily_report_constraints = inspect(engine).get_unique_constraints("daily_reports")
        assert all(
            item.get("name") != "uq_daily_report_class_date"
            for item in daily_report_constraints
        )
    finally:
        engine.dispose()
