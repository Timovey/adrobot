from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    keitaro_url: str = "https://tlgk.host"
    keitaro_api_key: str = Field(default="", repr=False)
    keitaro_timeout_seconds: float = 60.0
    database_url: str = "sqlite:///./data/adrobot.db"
    default_domain_id: int | None = None
    default_group_id: int | None = None
    default_traffic_source_id: int | None = None
    campaign_name_prefix: str = "test_timovey_"
    google_redirect_url: str = "https://www.google.com/"
    # When set, every /api/* route except /api/health requires
    # `Authorization: Bearer <token>` or `X-API-Key: <token>`.
    adrobot_api_token: str = Field(default="", repr=False)
    log_level: str = "INFO"

    @property
    def keitaro_api_base(self) -> str:
        return f"{self.keitaro_url.rstrip('/')}/admin_api/v1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
