import os
from typing import List, Union
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    BOT_TOKEN: str
    ALLOWED_TELEGRAM_USER_IDS: List[int] = []
    
    GROQ_API_KEY: str
    GEMINI_API_KEY: str = ""
    
    WEBDAV_HOSTNAME: str = "https://webdav.cloud.mail.ru"
    WEBDAV_LOGIN: str
    WEBDAV_PASSWORD: str
    WEBDAV_VAULT_PATH: str = "/Vault"
    
    GOOGLE_CALENDAR_ID: str = "primary"
    GOOGLE_SERVICE_ACCOUNT_FILE: str = "credentials/service_account.json"
    
    DATABASE_PATH: str = "data/scheduler.db"
    TEMPLATE_PATH: str = "To-Do template.md"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @field_validator("ALLOWED_TELEGRAM_USER_IDS", mode="before")
    @classmethod
    def parse_user_ids(cls, v: Union[str, List[int], List[str]]) -> List[int]:
        if isinstance(v, str):
            if not v.strip():
                return []
            return [int(uid.strip()) for uid in v.split(",") if uid.strip()]
        if isinstance(v, list):
            return [int(uid) for uid in v]
        return []


settings = Settings()
