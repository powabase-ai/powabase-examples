"""Application settings, loaded from the environment (Pydantic v2)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Backend configuration.

    All Powabase values come from the project's Connect modal. The Service Role
    key and Database URL are SERVER-SIDE ONLY — never expose them to the browser.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Powabase project ---
    # Project URL, e.g. https://YOUR_REF.p.powabase.ai
    powabase_base_url: str = ""
    # Service Role (Secret) key — server-side only.
    powabase_service_role_key: str = ""
    # Direct Postgres connection string (Database URL from the Connect modal).
    powabase_database_url: str = ""
    # JWT secret, for verifying end-user GoTrue tokens on the backend.
    powabase_jwt_secret: str = ""

    # Powabase resource ids wired up in the project (set once provisioned).
    research_agent_id: str = ""
    generation_workflow_id: str = ""
    brand_kb_id: str = ""

    # --- Server ---
    cors_allow_origins: str = "http://localhost:3000"
    # Public base URL of the frontend, used to build crawlable /p/{id} links.
    public_base_url: str = "http://localhost:3007"
    db_pool_min_size: int = 1
    db_pool_max_size: int = 10

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
