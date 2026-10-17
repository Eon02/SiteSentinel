from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Supabase connection string (Project Settings -> Database -> Connection string).
    # Plain "postgresql://" URLs are accepted and converted to the asyncpg driver.
    database_url: str
    open_meteo_url: str = "https://api.open-meteo.com/v1/forecast"
    fema_nfhl_url: str = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer"
    nws_url: str = "https://api.weather.gov"
    nws_user_agent: str = "SiteSentinel (github.com/Willsbam/SiteSentinel)"

    # Next-day briefings. Off by default so dev machines don't claim and send the team's
    # briefings; turn on in exactly one deployed instance.
    briefings_enabled: bool = False
    briefing_webhook_url: str | None = None  # where briefings are POSTed as JSON
    briefing_webhook_token: str | None = None  # sent as "Authorization: Bearer <token>"
    briefing_hour: int = 18  # site-local hour when the scheduled briefing goes out
    briefing_check_interval_s: int = 300

    # Conditions worker: records current weather per site into `conditions`. Off by default;
    # turn on in one deployed instance (or run scripts.record_conditions from cron instead).
    conditions_enabled: bool = False
    conditions_interval_s: int = 900  # Open-Meteo updates current weather every 15 minutes
    # Outgoing email (flood warnings). Any SMTP provider works, e.g. Gmail with an app
    # password (smtp.gmail.com:587), Outlook, SendGrid or Mailtrap for testing.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None  # e.g. "SiteSentinel <alerts@example.com>"; defaults to username
    smtp_starttls: bool = True  # STARTTLS on 587; set false and port 465 for implicit TLS

    # Automated flood checks: email a site's workers when its flood warning is first raised.
    # Off by default; turn on in exactly one deployed instance (like BRIEFINGS_ENABLED).
    flood_checks_enabled: bool = False
    flood_check_interval_s: int = 1800
    flood_rain_threshold_in: float = 1.0
    cors_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:3000",
        "http://localhost:5173",
    ]

    @field_validator("database_url")
    @classmethod
    def use_asyncpg_driver(cls, value: str) -> str:
        for prefix in ("postgresql://", "postgres://"):
            if value.startswith(prefix):
                return "postgresql+asyncpg://" + value.removeprefix(prefix)
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
