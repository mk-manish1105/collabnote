from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List
from dotenv import load_dotenv
import os

# Force load .env file
load_dotenv()


class Settings(BaseSettings):
    # Tell Pydantic where .env is
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True
    )

    # App
    APP_NAME: str = "CollabNote Pro"
    APP_VERSION: str = "2.0.0"
    DEBUG: bool = True

    # Database (NO hardcoded wrong password)
    DATABASE_URL: str

    # JWT
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # CORS
    ALLOWED_ORIGINS: List[str] = ["*"]

    # Auto-save
    AUTO_SAVE_INTERVAL_SECONDS: int = 30

    # Version history
    MAX_VERSIONS_PER_DOCUMENT: int = 50


# Create instance
settings = Settings()

# Debug print (REMOVE later)
print("✅ Loaded DB URL:", settings.DATABASE_URL)