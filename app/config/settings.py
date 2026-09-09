from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Все настройки приходят из .env (единственный источник конфигурации)."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_env: str = "development"
    app_base_url: str = "http://localhost:8000"
    secret_key: str = "dev-secret-change-me"

    # Шифрование Google-токенов в БД (Fernet, ключ = urlsafe-base32 от ENCRYPTION_KEY)
    encryption_key: str = ""

    database_url: str = "postgresql+psycopg://booking:booking@localhost:5432/booking"
    redis_url: str = "redis://localhost:6379/0"

    # Telegram
    bot_token: str = ""
    bot_webhook_secret: str = ""

    # OpenRouter (AI)
    openrouter_api_key: str = ""
    openrouter_model: str = "qwen/qwen3-max"
    openrouter_temperature: float = 0.2
    openrouter_max_tokens: int = 2000
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/oauth/google/callback"

    timezone: str = "Europe/Moscow"

    @property
    def is_prod(self) -> bool:
        return self.app_env == "production"

    @property
    def encryption_ready(self) -> bool:
        return bool(self.encryption_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
