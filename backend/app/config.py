from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://odc:odc@localhost:5432/meeting_room"
    odc_timezone: str = "America/Sao_Paulo"
    reminder_lead_minutes: int = 15

    openai_api_key: str = ""
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_deployment: str = ""
    azure_openai_api_version: str = "2024-02-15-preview"

    brevo_api_key: str = ""
    brevo_sender_email: str = ""
    brevo_sender_name: str = "ODC Meeting Room"
    brevo_template_booking_confirmed: str = ""
    brevo_template_booking_extended: str = ""
    brevo_template_booking_cancelled: str = ""
    brevo_template_vacate_reminder: str = ""
    brevo_template_waitlist_available: str = ""

    teams_webhook_url: str = ""
    teams_graph_tenant_id: str = ""
    teams_graph_client_id: str = ""
    teams_graph_client_secret: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
