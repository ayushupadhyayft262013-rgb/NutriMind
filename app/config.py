"""Centralized configuration loaded from environment variables."""

import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    """Application settings loaded from .env file."""

    # Telegram
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

    # Google Gemini
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

    # Notion
    NOTION_API_KEY: str = os.getenv("NOTION_API_KEY", "")
    NOTION_DAILY_LOG_DB_ID: str = os.getenv("NOTION_DAILY_LOG_DB_ID", "")
    NOTION_PARENT_PAGE_ID: str = os.getenv("NOTION_PARENT_PAGE_ID", "")

    # Webhook
    WEBHOOK_BASE_URL: str = os.getenv("WEBHOOK_BASE_URL", "http://localhost:8000")

    # Defaults
    DEFAULT_TARGET_KCAL: int = int(os.getenv("DEFAULT_TARGET_KCAL", "1800"))
    DEFAULT_TARGET_PROTEIN: int = int(os.getenv("DEFAULT_TARGET_PROTEIN", "130"))

    # Internal
    SQLITE_DB_PATH: str = os.getenv("SQLITE_DB_PATH", "nutrimind.db")

    def validate(self) -> list[str]:
        """Return a list of missing required config keys."""
        missing = []
        if not self.TELEGRAM_BOT_TOKEN:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not self.GEMINI_API_KEY:
            missing.append("GEMINI_API_KEY")
        if not self.NOTION_API_KEY:
            missing.append("NOTION_API_KEY")
        return missing


settings = Settings()
