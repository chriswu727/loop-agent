"""Typed application configuration (12-factor).

Nothing in the codebase reads ``os.environ`` directly — everything goes through
the single :data:`settings` object so configuration is validated once, at boot,
and is fully type-checked everywhere it is used.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, RedisDsn, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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
    loop_revision: str = "unknown"

    # ---- HTTP server ----
    # Bind to loopback by default: the agent can run shell commands, so the API
    # must not be network-reachable out of the box. Containers set API_HOST=0.0.0.0.
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        raw = value.strip()
        if raw.startswith("["):
            decoded = json.loads(raw)
            if not isinstance(decoded, list):
                raise ValueError("CORS_ORIGINS JSON must be a list")
            return decoded
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    # ---- Security ----
    secret_key: str = "change-me-in-production-use-openssl-rand-hex-32"
    access_token_expire_minutes: int = 30
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "loop-web"
    jwt_audience: str = "loop-api"
    # When set, every /api/v1 route requires `Authorization: Bearer <token>`
    # (health stays open). Unset = open, only safe on a trusted/loopback network.
    api_token: str | None = None
    auth_required: bool = False

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
    redis_url: RedisDsn = Field(default="redis://localhost:6379/0")  # type: ignore[assignment]
    # "auto" uses an in-memory cache on the zero-infra (SQLite) path and Redis
    # otherwise; force either explicitly when you need to.
    cache_backend: Literal["auto", "redis", "memory"] = "auto"

    # ---- Observability ----
    otel_exporter_otlp_endpoint: str | None = None
    prometheus_enabled: bool = True

    # ---- LLM providers (cascade: primary first, others as fallback) ----
    anthropic_api_key: str | None = None
    deepseek_api_key: str | None = None
    # deepseek-chat (V3, fast/cheap) by default; set deepseek-reasoner (R1) for the
    # stronger, pricier reasoning model.
    deepseek_model: str = "deepseek-chat"
    gemini_api_key: str | None = None
    glm_api_key: str | None = None
    # Local, fully-offline model via Ollama. Set the base URL to enable it (it has
    # no API key); pick the model with OLLAMA_MODEL.
    ollama_base_url: str | None = None
    ollama_model: str = "llama3.2"
    # Zero-key demo: a deterministic scripted "model" that drives one real,
    # verified task (writes + runs fib.py) so a fresh clone shows the full loop
    # and a Receipt with no API key. Enable with DEMO_MODE=1.
    demo_mode: bool = False
    llm_default_provider: Literal["anthropic", "deepseek", "gemini", "glm", "ollama", "mock"] = (
        "deepseek"
    )
    llm_verifier_provider: (
        Literal["anthropic", "deepseek", "gemini", "glm", "ollama", "mock"] | None
    ) = None
    llm_timeout_seconds: int = 120
    # Retry a retryable failure (timeout, 5xx, empty) on the same provider before
    # cascading — one transient blip shouldn't fail a whole task. A mid-run failure
    # is expensive: it discards a partially-complete run (the model may already have
    # written correct output), so the budget is set to ride out a multi-second
    # overload of a reasoning model, not just an instant blip. With these defaults:
    # 5 attempts, linear backoff summing to ~7.5s. Bounded so a negative value can't
    # make complete() skip every provider (client asserts >=1 attempt).
    llm_max_retries: int = Field(default=4, ge=0)
    llm_retry_backoff_seconds: float = Field(default=0.75, ge=0.0)

    # ---- Agent loop limits (the "within the limit" guardrails) ----
    # Defaults are what a new task starts with; caps are the hard ceilings a
    # user cannot exceed, so a single task can never run away with cost.
    execution_mode: Literal["inline", "worker"] = "inline"
    # Cap concurrent inline runs: each pins a DB connection for its whole run, so
    # without a bound a burst of publishes exhausts the pool. Excess runs queue.
    agent_max_concurrent_runs: int = Field(default=8, ge=1, le=256)
    # A separate DB session watches a running task so API cancellation interrupts
    # an in-flight provider/tool call instead of waiting for the next agent step.
    agent_cancellation_poll_seconds: float = Field(default=0.25, ge=0.05, le=5.0)
    # A task RUNNING with no update for longer than this is treated as stranded by a
    # crash and failed on reconcile. Must exceed the longest gap between step commits
    # (one LLM call + one command + retries), so a live run is never wrongly failed.
    worker_stale_task_seconds: int = Field(default=900, ge=60)
    worker_visibility_timeout_seconds: int = Field(default=900, ge=30)
    worker_max_attempts: int = Field(default=3, ge=1, le=20)
    worker_queue_max_length: int = Field(default=100_000, ge=1_000)
    agent_max_steps_default: int = 12
    agent_max_steps_cap: int = 40
    # Mask secret-shaped strings in tool observations before they reach the model,
    # the ledger, or the API. On by default (Loop's "don't leak secrets" posture).
    agent_redact_secrets: bool = True
    loop_token_budget_default: int = 60_000
    loop_token_budget_cap: int = 200_000

    # ---- Agent tools & safety ----
    # Each task works inside its own subdirectory under this root. In inline mode
    # (no container), point this OUTSIDE any project directory: tools the agent runs
    # (pytest, ruff, mypy) walk up for a config file, so a workspace nested under a
    # Python project would inherit that project's pyproject.toml and misbehave.
    # Container mode is unaffected (the workspace is the mount root).
    agent_workspaces_root: str = "./workspaces"
    # Optional local-project boundary. When set, a task may bind to one clean Git
    # repository below this root; Loop clones it into the task workspace and never
    # gives the agent a path back to the source checkout.
    loop_local_projects_root: str | None = None
    loop_changeset_preview_bytes: int = Field(default=200_000, ge=10_000, le=5_000_000)
    # Owner/project-scoped cross-task memory store (MEMORY.md + topics/).
    agent_memory_root: str = "./agent_memory"
    # Signed skills: a folder of skill bundles, and the ed25519 trust public key
    # a skill's signature must verify against to be loadable. The key can be an
    # inline PEM, or (cleaner for a multi-line PEM) a file path — defaulting to the
    # committed dev trust root so the bundled example skill works out of the box.
    agent_skills_root: str = "./skills"
    agent_skill_trust_public_key: str | None = None
    agent_skill_trust_public_key_file: str | None = "./skills/trust_key.pem"

    def trust_public_key_pem(self) -> str | None:
        if self.agent_skill_trust_public_key:
            return self.agent_skill_trust_public_key
        if self.agent_skill_trust_public_key_file:
            try:
                from pathlib import Path

                return Path(self.agent_skill_trust_public_key_file).read_text()
            except OSError:
                return None
        return None

    # Optional ed25519 key the server signs Receipts with. Unset = Receipts are
    # tamper-EVIDENT (content hash) but not tamper-PROOF; set it for high-assurance
    # deployments so a workspace-writer without the private key can't forge one.
    # `make receipt-keygen` writes a key here.
    agent_receipt_signing_key: str | None = None
    agent_receipt_signing_key_file: str | None = None

    def receipt_signing_key_pem(self) -> str | None:
        if self.agent_receipt_signing_key:
            return self.agent_receipt_signing_key
        if self.agent_receipt_signing_key_file:
            try:
                from pathlib import Path

                return Path(self.agent_receipt_signing_key_file).read_text()
            except OSError:
                return None
        return None

    # Separate Ed25519 issuer for short-lived task authority. The worker holds
    # this private key; both gateways and the egress proxy receive only its public
    # key, so no enforcement service can mint broader authority.
    agent_authority_signing_key: str | None = None
    agent_authority_signing_key_file: str | None = None
    agent_authority_token_ttl_seconds: int = Field(default=300, ge=30, le=900)

    def authority_signing_key_pem(self) -> str | None:
        if self.agent_authority_signing_key:
            return self.agent_authority_signing_key
        if self.agent_authority_signing_key_file:
            try:
                from pathlib import Path

                return Path(self.agent_authority_signing_key_file).read_text()
            except OSError:
                return None
        return None

    agent_command_timeout_seconds: int = 60
    agent_command_output_limit: int = 4_000  # chars of command output kept
    agent_max_upload_bytes: int = Field(default=10_000_000, ge=1_024, le=1_000_000_000)
    agent_max_workspace_bytes: int = Field(default=100_000_000, ge=1_024, le=10_000_000_000)
    # auto  = run allowlisted/unknown commands, hard-block dangerous ones
    # manual = additionally refuse non-allowlisted commands (await approval)
    agent_approval_mode: Literal["auto", "manual"] = "auto"
    agent_acceptance_score: int = 70  # verifier score needed to accept "finish"
    agent_max_finish_retries: int = 2  # times a rejected finish is pushed back
    agent_stuck_threshold: int = 4  # consecutive failed/blocked steps -> give up
    agent_max_spawn_depth: int = 2  # how deep sub-agents may delegate further
    agent_repeated_action_limit: int = Field(default=2, ge=1, le=10)
    agent_exploration_branch_cap: int = Field(default=8, ge=1, le=20)
    agent_verification_token_reserve: int = Field(default=16_000, ge=500)

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

    # ---- Chat inlet (Slack Events API). Set both to enable POST /slack/events. ----
    # The signing secret authenticates every request (only your Slack app can call
    # in); the bot token posts replies. An optional channel allowlist adds defense
    # in depth — the bot can run code, so restrict it in a shared workspace.
    slack_bot_token: str | None = None
    slack_signing_secret: str | None = None
    slack_allowed_channels: str | None = None
    slack_allow_public: bool = False

    @property
    def slack_configured(self) -> bool:
        return bool(self.slack_bot_token and self.slack_signing_secret)

    def slack_allowlist(self) -> set[str]:
        raw = self.slack_allowed_channels or ""
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
    agent_allow_host_providers: bool = True
    agent_browser_command: str = "npx -y @playwright/mcp@0.0.78 --headless --isolated"
    agent_sibyl_enabled: bool = False
    agent_sibyl_command: str = "sibyl-mcp"
    agent_argus_enabled: bool = False
    agent_argus_command: str = "argus-mcp --tool-profile core"
    # Legacy shared endpoint remains available outside production for upgrades.
    agent_provider_gateway_url: str | None = None
    agent_browser_gateway_url: str | None = None
    agent_email_gateway_url: str | None = None
    agent_calendar_gateway_url: str | None = None
    agent_vision_gateway_url: str | None = None
    agent_email_egress_hosts: str = ""
    agent_calendar_egress_hosts: str = ""
    agent_vision_egress_hosts: str = "generativelanguage.googleapis.com"

    # Destination-enforcing proxy used by shell and isolated provider runtimes.
    # The sandbox joins an internal-only network where this proxy is the sole
    # route out; short-lived authority tokens carry the exact host allowlist.
    agent_egress_proxy_url: str | None = None
    agent_egress_proxy_audit_url: str | None = None
    agent_egress_docker_network: str = "loop_sandbox-egress"
    agent_require_egress_hosts: bool = True

    # ---- Sandbox: run the agent's shell commands in an ephemeral container ----
    # required = fail the task if isolation is unavailable; preferred = use a
    # container when available and explicitly label the local fallback; off = host.
    # Legacy auto/container/inline values remain accepted for configuration upgrades.
    agent_sandbox: Literal["required", "preferred", "off", "auto", "container", "inline"] = (
        "preferred"
    )
    agent_sandbox_image: str = "loop-sandbox:latest"
    agent_sandbox_image_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    agent_sandbox_memory: str = "512m"
    agent_sandbox_cpus: str = "1"
    agent_sandbox_backend: Literal["auto", "docker", "kubernetes"] = "auto"
    agent_docker_workspace_volume: str | None = None
    agent_docker_workspace_mount: str = "/var/lib/loop"
    agent_kubernetes_namespace: str = "loop"
    agent_kubernetes_data_pvc: str = "loop-data"
    agent_kubernetes_data_mount: str = "/var/lib/loop"

    @model_validator(mode="after")
    def validate_production_security(self) -> Settings:
        if not self.is_production:
            return self
        if not self.auth_required:
            raise ValueError("AUTH_REQUIRED must be true in production")
        if len(self.secret_key) < 32 or "change_me" in self.secret_key.lower():
            raise ValueError("SECRET_KEY must be a non-placeholder value of at least 32 bytes")
        if self.agent_sandbox not in {"required", "container"}:
            raise ValueError("production requires AGENT_SANDBOX=required")
        if not self.agent_sandbox_image_digest:
            raise ValueError("production requires an immutable sandbox image digest")
        if self.agent_allow_host_providers:
            raise ValueError("production host providers must be disabled")
        if self.agent_sibyl_enabled or self.agent_argus_enabled:
            raise ValueError("production host MCP providers must be disabled")
        if self.agent_provider_gateway_url:
            raise ValueError("production forbids the legacy shared Provider Gateway")
        if not self.agent_email_gateway_url or not self.agent_email_egress_hosts.strip():
            raise ValueError("production requires an isolated Email Gateway and egress hosts")
        if not self.agent_calendar_gateway_url or not self.agent_calendar_egress_hosts.strip():
            raise ValueError("production requires an isolated Calendar Gateway and egress hosts")
        if not self.agent_vision_gateway_url or not self.agent_vision_egress_hosts.strip():
            raise ValueError("production requires an isolated Vision Gateway and egress hosts")
        if not self.agent_browser_gateway_url:
            raise ValueError("production requires an isolated Browser Gateway")
        if not self.agent_egress_proxy_url or not self.agent_egress_proxy_audit_url:
            raise ValueError("production requires a destination-enforcing egress proxy")
        if self.service_name == "worker":
            authority_key = self.authority_signing_key_pem()
            if not authority_key:
                raise ValueError("production worker requires an Ed25519 authority signing key")
            try:
                from app.domain.authority_token import public_key_pem

                public_key_pem(authority_key)
            except ValueError as exc:
                raise ValueError("Authority signing key must be a valid Ed25519 PEM key") from exc
        receipt_key = self.receipt_signing_key_pem()
        if not receipt_key:
            raise ValueError("production requires an Ed25519 Receipt signing key")
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            from cryptography.hazmat.primitives.serialization import load_pem_private_key

            parsed_key = load_pem_private_key(receipt_key.encode(), password=None)
        except (OSError, TypeError, ValueError) as exc:
            raise ValueError("Receipt signing key is not a valid unencrypted PEM key") from exc
        if not isinstance(parsed_key, Ed25519PrivateKey):
            raise ValueError("Receipt signing key must be Ed25519")
        return self

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
