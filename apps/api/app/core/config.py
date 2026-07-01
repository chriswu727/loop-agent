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
    # Bind to loopback by default: the agent can run shell commands, so the API
    # must not be network-reachable out of the box. Containers set API_HOST=0.0.0.0.
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # ---- Security ----
    secret_key: str = "change-me-in-production-use-openssl-rand-hex-32"
    access_token_expire_minutes: int = 30
    jwt_algorithm: str = "HS256"
    # When set, every /api/v1 route requires `Authorization: Bearer <token>`
    # (health stays open). Unset = open, only safe on a trusted/loopback network.
    api_token: str | None = None

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
    anthropic_api_key: str | None = None
    deepseek_api_key: str | None = None
    gemini_api_key: str | None = None
    glm_api_key: str | None = None
    # Local, fully-offline model via Ollama. Set the base URL to enable it (it has
    # no API key); pick the model with OLLAMA_MODEL.
    ollama_base_url: str | None = None
    ollama_model: str = "llama3.2"
    llm_default_provider: Literal["anthropic", "deepseek", "gemini", "glm", "ollama"] = "deepseek"
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
    # Cross-task memory store (MEMORY.md + topics/), shared across tasks.
    agent_memory_root: str = "./agent_memory"
    # Signed skills: a folder of skill bundles, and the ed25519 trust public key
    # (PEM) a skill's signature must verify against to be loadable.
    agent_skills_root: str = "./skills"
    agent_skill_trust_public_key: str | None = None
    agent_command_timeout_seconds: int = 60
    agent_command_output_limit: int = 4_000  # chars of command output kept
    # auto  = run allowlisted/unknown commands, hard-block dangerous ones
    # manual = additionally refuse non-allowlisted commands (await approval)
    agent_approval_mode: Literal["auto", "manual"] = "auto"
    agent_acceptance_score: int = 70  # verifier score needed to accept "finish"
    agent_max_finish_retries: int = 2  # times a rejected finish is pushed back
    agent_stuck_threshold: int = 4  # consecutive failed/blocked steps -> give up
    agent_max_spawn_depth: int = 2  # how deep sub-agents may delegate further

    # ---- Trigger heartbeat (scheduled firing) ----
    scheduler_enabled: bool = True
    scheduler_tick_seconds: int = 60

    # ---- Chat inlet (Telegram). Set the bot token to enable. ----
    telegram_bot_token: str | None = None
    # Comma-separated chat ids allowed to command the bot. The bot can run code and
    # send email, so with no allowlist it refuses to start unless you explicitly
    # opt into a public bot below.
    telegram_allowed_chat_ids: str | None = None
    telegram_allow_public: bool = False

    def telegram_allowlist(self) -> set[str]:
        raw = self.telegram_allowed_chat_ids or ""
        return {c.strip() for c in raw.split(",") if c.strip()}

    # ---- Email (a task opts in with use_email; needs SMTP/IMAP creds) ----
    # For Gmail: smtp.gmail.com:587 + imap.gmail.com, user = address, password =
    # an app password. Email is "configured" when host + user + password are set.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = True
    imap_host: str | None = None
    email_from: str | None = None

    @property
    def email_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_password)

    # ---- Calendar over CalDAV (a task opts in with use_calendar) ----
    # Works with iCloud / Fastmail / Nextcloud / etc. via an app password.
    caldav_url: str | None = None
    caldav_user: str | None = None
    caldav_password: str | None = None
    caldav_calendar: str | None = None  # pick a calendar by name; else the first

    @property
    def calendar_configured(self) -> bool:
        return bool(self.caldav_url and self.caldav_user and self.caldav_password)

    # ---- MCP: headless browser (a task opts in with use_browser) ----
    agent_browser_enabled: bool = True
    agent_browser_command: str = "npx -y @playwright/mcp@latest --headless --isolated"

    # ---- Sandbox: run the agent's shell commands in an ephemeral container ----
    # auto = container when Docker + the image are available, else inline (labeled);
    # container = require the container (fall back to inline, labeled, if missing);
    # inline = always run on the host (zero-infra, reduced isolation).
    agent_sandbox: Literal["auto", "container", "inline"] = "auto"
    agent_sandbox_image: str = "loop-sandbox:latest"
    agent_sandbox_memory: str = "512m"
    agent_sandbox_cpus: str = "1"

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
