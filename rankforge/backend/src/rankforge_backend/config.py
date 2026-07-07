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
    # Public base URL of the frontend, used to build crawlable /p/{id} links. Must
    # match where the frontend actually serves (compose/Dockerfile/CORS → :3000),
    # else out-of-the-box public/canonical/webhook URLs point at a dead port.
    public_base_url: str = "http://localhost:3000"
    db_pool_min_size: int = 2
    db_pool_max_size: int = 20
    # Seconds a request waits for a free pooled connection before the pool raises
    # PoolTimeout (surfaced as 503, not a hung request or a 500).
    db_pool_timeout: float = 15.0
    # Max concurrent heavy background tasks (generation/research/refine/scout).
    # Kept well under the DB pool size so a burst can't starve the pool.
    max_background_tasks: int = 4
    # Per-user rate limit on expensive AI operations (generate / refine / research
    # / optimize / score / scout-run / opportunity-draft): N requests per window.
    rate_limit_expensive: int = 30
    rate_limit_window_seconds: float = 60.0
    # Attempts per window a single (authenticated) account may make against the signup
    # invite-code endpoint — brute-force protection for the shared code. Kept low.
    rate_limit_invite: int = 10

    # --- Signup gate ---
    # Shared invite code that a newly-registered account must redeem once before it can
    # use the app. When EMPTY the gate is DISABLED (open signup) — set this in production
    # to close signups. Treat it like a password; rotate by changing the env value.
    signup_invite_code: str = ""

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
