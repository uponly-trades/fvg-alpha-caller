from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str
    MASTER_ENCRYPTION_KEY: str  # base64-encoded 32 bytes
    BINANCE_PROXY_URL: str | None = None
    INTERNAL_TOKEN: str
    TELEGRAM_BOT_TOKEN: str
    HTTP_PORT: int = 8014
    SIGNAL_POLL_INTERVAL_S: float = 2.0
    PNL_RECONCILE_INTERVAL_S: float = 60.0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
