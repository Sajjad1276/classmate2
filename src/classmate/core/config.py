from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = Field("", alias="BOT_TOKEN")
    admin_bot_token: str = Field("", alias="TELEGRAM_BOT_TOKEN")
    database_url: str = Field(
        "postgresql+asyncpg://classmate:classmate@localhost:5432/classmate",
        alias="DATABASE_URL",
    )
    redis_url: str | None = Field(None, alias="REDIS_URL")
    bootstrap_admin_ids: str = Field("", alias="BOOTSTRAP_ADMIN_IDS")
    environment: str = Field("development", alias="ENVIRONMENT")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    @property
    def bootstrap_admin_id_set(self) -> set[int]:
        if not self.bootstrap_admin_ids.strip():
            return set()
        return {
            int(x.strip())
            for x in self.bootstrap_admin_ids.split(",")
            if x.strip().isdigit()
        }

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
