# 🔹 FILE: app/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    # اقرأ من .env وتجاهل أي مفاتيح زيادة بدل ما يرمي Error
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",
        extra="ignore",
    )

    # --- APP ---
    APP_NAME: str = "AUJI API"
    APP_ENV: str = "dev"

    # --- SECURITY ---
    SECRET_KEY: str = "change-me-please"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # --- DATABASE ---
    DATABASE_URL: str = "sqlite:///./auji.db"

    # --- OPENROUTER ---
    OPENROUTER_API_KEY: str | None = Field(default=None)
    OPENROUTER_MODEL: str | None = Field(default="deepseek/deepseek-r1-0528:free")

settings = Settings()
