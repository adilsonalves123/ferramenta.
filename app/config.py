from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PASTA = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    database_url: str
    openai_api_key: str
    openai_model: str = "gpt-4o-mini"
    openai_image_model: str = "gpt-image-2.5-flare"
    cors_origins: str = "http://localhost:3000,https://www.riscaai.com.br"
    meta_app_id: str = ""
    meta_app_secret: str = ""
    instagram_app_id: str = ""
    instagram_app_secret: str = ""
    google_places_api_key: str = ""
    google_weather_api_key: str = ""
    feriados_api_token: str = ""
    app_url: str = "https://ferramenta-ooun.onrender.com"
    produto_nome: str = "Risca Aí Posts"
    empresa_nome: str = "Risca Aí"
    empresa_cnpj: str = ""
    contato_email: str = ""

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
