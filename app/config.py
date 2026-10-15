from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PASTA = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    database_url: str
    openai_api_key: str
    openai_model: str = "gpt-4o-mini"
    cors_origins: str = "http://localhost:3000,https://www.riscaai.com.br"

    model_config = SettingsConfigDict(
        env_file=PASTA / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def origens_cors(self) -> list[str]:
        return [origem.strip() for origem in self.cors_origins.split(",") if origem.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
