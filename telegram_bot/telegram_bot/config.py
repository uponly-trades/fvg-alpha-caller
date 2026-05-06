from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str
    TELEGRAM_BOT_TOKEN: str
    EXECUTOR_URL: str = "http://trade_executor:8014"
    INTERNAL_TOKEN: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
