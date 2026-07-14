from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class EgressProxySettings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore", populate_by_name=True)

    host: str = Field(default="0.0.0.0", validation_alias="EGRESS_PROXY_HOST")
    port: int = Field(default=8080, validation_alias="EGRESS_PROXY_PORT")
    admin_host: str = Field(default="0.0.0.0", validation_alias="EGRESS_PROXY_ADMIN_HOST")
    admin_port: int = Field(default=8081, validation_alias="EGRESS_PROXY_ADMIN_PORT")
    connect_timeout_seconds: int = Field(
        default=20, ge=1, le=60, validation_alias="EGRESS_PROXY_CONNECT_TIMEOUT_SECONDS"
    )
    allowed_ports: str = Field(default="80,443", validation_alias="EGRESS_PROXY_ALLOWED_PORTS")
    require_authority_key: bool = Field(
        default=False, validation_alias="EGRESS_PROXY_REQUIRE_AUTHORITY_KEY"
    )
    authority_public_key: str | None = Field(
        default=None, validation_alias="EGRESS_PROXY_AUTHORITY_PUBLIC_KEY"
    )
    authority_public_key_file: str | None = Field(
        default=None, validation_alias="EGRESS_PROXY_AUTHORITY_PUBLIC_KEY_FILE"
    )
    audit_database_path: str | None = Field(
        default=None, validation_alias="EGRESS_PROXY_AUDIT_DATABASE_PATH"
    )
    require_durable_audit: bool = Field(
        default=False, validation_alias="EGRESS_PROXY_REQUIRE_DURABLE_AUDIT"
    )
    audit_max_events_per_run: int = Field(
        default=200,
        ge=1,
        le=10_000,
        validation_alias="EGRESS_PROXY_AUDIT_MAX_EVENTS_PER_RUN",
    )
    audit_max_events_total: int = Field(
        default=50_000,
        ge=1,
        le=1_000_000,
        validation_alias="EGRESS_PROXY_AUDIT_MAX_EVENTS_TOTAL",
    )

    def public_key_pem(self) -> str | None:
        if self.authority_public_key:
            return self.authority_public_key
        if self.authority_public_key_file:
            try:
                return Path(self.authority_public_key_file).read_text()
            except OSError:
                return None
        return None

    def allowed_port_set(self) -> set[int]:
        ports: set[int] = set()
        for value in self.allowed_ports.split(","):
            try:
                port = int(value.strip())
            except ValueError:
                continue
            if 1 <= port <= 65535:
                ports.add(port)
        return ports
