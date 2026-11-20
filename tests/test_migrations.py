"""Fresh database and repeatability smoke test for Alembic."""

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect


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
        }.issubset(tables)
        assert "alembic_version" in tables
    finally:
        engine.dispose()



def test_initial_migration_snapshot_is_frozen():
    from classmate.db.models import Base
    from classmate.db.schema_v0001 import SnapshotBase

    assert "telegram_outbox" in Base.metadata.tables
    assert "telegram_outbox" not in SnapshotBase.metadata.tables
