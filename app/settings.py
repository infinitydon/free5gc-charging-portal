from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    mongo_uri: str = Field(default="mongodb://mongodb:27017", alias="MONGO_URI")
    mongo_db: str = Field(default="free5gc", alias="MONGO_DB")
    chf_base_url: str = Field(default="http://chf:8000", alias="CHF_BASE_URL")
    chf_notify_enabled: bool = Field(default=True, alias="CHF_NOTIFY_ENABLED")
    chf_bearer_token: str = Field(default="", alias="CHF_BEARER_TOKEN")
    portal_title: str = Field(default="free5GC Charging Portal", alias="PORTAL_TITLE")
    portal_mode: str = Field(default="operator", alias="PORTAL_MODE")
    operator_pin: str = Field(default="admin123", alias="OPERATOR_PIN")
    end_user_self_topup: bool = Field(default=True, alias="END_USER_SELF_TOPUP")
    trusted_subscriber_header_enabled: bool = Field(default=False, alias="TRUSTED_SUBSCRIBER_HEADER_ENABLED")
    trusted_subscriber_header: str = Field(default="x-subscriber-supi", alias="TRUSTED_SUBSCRIBER_HEADER")
    subscriber_bindings_json: str = Field(default="{}", alias="SUBSCRIBER_BINDINGS_JSON")
    default_subscriber_supi: str = Field(default="", alias="DEFAULT_SUBSCRIBER_SUPI")


@lru_cache
def get_settings() -> Settings:
    return Settings()
