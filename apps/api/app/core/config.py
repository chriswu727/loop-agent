"""Typed application configuration (12-factor).

Nothing in the codebase reads ``os.environ`` directly — everything goes through
the single :data:`settings` object so configuration is validated once, at boot,
and is fully type-checked everywhere it is used.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, RedisDsn, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "staging", "production"]


class Settings(BaseSettings):
    """All runtime configuration, sourced from the environment.

    Required values without defaults will cause a fast, explicit failure at
    startup if they are missing — which is exactly what you want.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Identity / environment ----
    environment: Environment = "development"
    service_name: str = Field(default="api", alias="OTEL_SERVICE_NAME")
    version: str = "0.1.0"

    # ---- HTTP server ----
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # ---- Security ----
    secret_key: str = "change-me-in-production-use-openssl-rand-hex-32"
    access_token_expire_minutes: int = 30
    jwt_algorithm: str = "HS256"

    # ---- Logging ----
    log_level: str = "info"
    log_format: Literal["console", "json"] = "console"

    # ---- Database ----
    # Postgres is the production default; a ``sqlite+aiosqlite://`` URL is
    # accepted too so the whole app runs on a laptop with zero infrastructure.
    database_url: str = "postgresql+asyncpg://app:app@localhost:5432/app"
    # Optional separate DSN for read replicas; falls back to the primary.
    database_read_url: str | None = None
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False

    # ---- Cache / broker ----
    redis_url: RedisDsn = Field(default="redis://localhost:6379/0")  # type: ignore[arg-type]
    # "auto" uses an in-memory cache on the zero-infra (SQLite) path and Redis
    # otherwise; force either explicitly when you need to.
    cache_backend: Literal["auto", "redis", "memory"] = "auto"

    # ---- Observability ----
    otel_exporter_otlp_endpoint: str | None = None
    prometheus_enabled: bool = True

    # ---- LLM providers (cascade: primary first, others as fallback) ----
    deepseek_api_key: str | None = None
    gemini_api_key: str | None = None
    glm_api_key: str | None = None
    llm_default_provider: Literal["deepseek", "gemini", "glm"] = "deepseek"
    llm_timeout_seconds: int = 120

    # ---- Agent loop limits (the "within the limit" guardrails) ----
    # Defaults are what a new task starts with; caps are the hard ceilings a
    # user cannot exceed, so a single task can never run away with cost.
    execution_mode: Literal["inline", "worker"] = "inline"
    agent_max_steps_default: int = 12
    agent_max_steps_cap: int = 40
    loop_token_budget_default: int = 60_000
    loop_token_budget_cap: int = 200_000

    # ---- Agent tools & safety ----
    # Each task works inside its own subdirectory under this root.
    agent_workspaces_root: str = "./workspaces"
    agent_command_timeout_seconds: int = 60
    agent_command_output_limit: int = 4_000  # chars of command output kept
    # auto  = run allowlisted/unknown commands, hard-block dangerous ones
    # manual = additionally refuse non-allowlisted commands (await approval)
    agent_approval_mode: Literal["auto", "manual"] = "auto"
    agent_acceptance_score: int = 70  # verifier score needed to accept "finish"
    agent_max_finish_retries: int = 2  # times a rejected finish is pushed back
    agent_stuck_threshold: int = 4  # consecutive failed/blocked steps -> give up

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def tracing_enabled(self) -> bool:
        return bool(self.otel_exporter_otlp_endpoint)

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def sqlalchemy_dsn(self) -> str:
        return str(self.database_url)

    @property
    def sqlalchemy_read_dsn(self) -> str:
        return str(self.database_read_url or self.database_url)

    def cors_origins_list(self) -> list[str]:
        # Allow comma-separated string from the environment as well as a list.
        if len(self.cors_origins) == 1 and "," in self.cors_origins[0]:
            return [o.strip() for o in self.cors_origins[0].split(",") if o.strip()]
        return self.cors_origins


@lru_cache
def get_settings() -> Settings:
    """Cached accessor so the environment is parsed exactly once per process."""
    return Settings()


settings = get_settings()
