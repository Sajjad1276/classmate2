from sqlalchemy.engine import make_url

from classmate.db.session import normalize_async_database_url


def test_plain_postgres_url_is_normalized_to_asyncpg() -> None:
    normalized = make_url(
        normalize_async_database_url("postgresql://user:pass@db.example:5432/classmate")
    )

    assert normalized.drivername == "postgresql+asyncpg"
    assert normalized.username == "user"
    assert normalized.password == "pass"
    assert normalized.host == "db.example"


def test_psycopg_url_is_normalized_to_asyncpg() -> None:
    normalized = make_url(
        normalize_async_database_url(
            "postgresql+psycopg://user:pass@db.example:5432/classmate"
        )
    )

    assert normalized.drivername == "postgresql+asyncpg"


def test_existing_asyncpg_url_is_preserved() -> None:
    database_url = "postgresql+asyncpg://user:pass@db.example:5432/classmate"

    assert normalize_async_database_url(database_url) == database_url


def test_sqlite_url_is_preserved() -> None:
    database_url = "sqlite+aiosqlite:///:memory:"

    assert normalize_async_database_url(database_url) == database_url
