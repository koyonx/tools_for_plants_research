from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    backend_host: str = "0.0.0.0"
    backend_port: int = 8001
    backend_log_level: str = "info"
    backend_cors_origins: str = "http://localhost:3000"

    supabase_internal_url: str = "http://supabase-kong:8000"
    supabase_public_url: str = "http://localhost:8000"
    anon_key: str = Field(default="")
    service_role_key: str = Field(default="")

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.backend_cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
