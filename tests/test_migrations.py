"""Fresh database and repeatability smoke test for Alembic."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_alembic_head_installs_and_reinstalls_on_empty_sqlite(tmp_path, monkeypatch):
    database_path = tmp_path / "fresh.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path}")
    monkeypatch.delenv("BOT_TOKEN", raising=False)

    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))

    command.upgrade(config, "head")
    # A second upgrade must be idempotent.
    command.upgrade(config, "head")
    # Exercise the complete downgrade/upgrade chain as well.
    command.downgrade(config, "base")
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database_path}")
    try:
        tables = set(inspect(engine).get_table_names())
        assert {
            "users",
            "announcements",
            "homework_submissions",
            "poll_responses",
            "reminder_jobs",
        }.issubset(tables)
        assert "alembic_version" in tables
    finally:
        engine.dispose()
