from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.authority_token import authority_key_id, authority_public_keyring


class ProviderGatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore", populate_by_name=True)

    host: str = Field(default="0.0.0.0", validation_alias="PROVIDER_GATEWAY_HOST")
    port: int = Field(default=8090, validation_alias="PROVIDER_GATEWAY_PORT")
    require_authority_key: bool = Field(
        default=False, validation_alias="PROVIDER_GATEWAY_REQUIRE_AUTHORITY_KEY"
    )
    authority_public_key: str | None = Field(
        default=None, validation_alias="PROVIDER_GATEWAY_AUTHORITY_PUBLIC_KEY"
    )
    authority_public_key_file: str | None = Field(
        default=None, validation_alias="PROVIDER_GATEWAY_AUTHORITY_PUBLIC_KEY_FILE"
    )
    authority_public_keys: dict[str, str] = Field(
        default_factory=dict, validation_alias="PROVIDER_GATEWAY_AUTHORITY_PUBLIC_KEYS"
    )
    authority_public_keys_file: str | None = Field(
        default=None, validation_alias="PROVIDER_GATEWAY_AUTHORITY_PUBLIC_KEYS_FILE"
    )
    revocation_database_path: str | None = Field(
        default=None, validation_alias="PROVIDER_GATEWAY_REVOCATION_DATABASE_PATH"
    )
    require_durable_revocations: bool = Field(
        default=False, validation_alias="PROVIDER_GATEWAY_REQUIRE_DURABLE_REVOCATIONS"
    )
    egress_proxy_url: str | None = Field(
        default=None, validation_alias="PROVIDER_GATEWAY_EGRESS_PROXY_URL"
    )
    egress_proxy_service_host: str | None = Field(
        default=None, validation_alias="EGRESS_PROXY_SERVICE_HOST"
    )
    egress_proxy_service_port: int = Field(
        default=8080, ge=1, le=65535, validation_alias="EGRESS_PROXY_SERVICE_PORT_PROXY"
    )
    authority_audience: str = Field(
        default="loop-provider-gateway",
        validation_alias="PROVIDER_GATEWAY_AUTHORITY_AUDIENCE",
    )
    service_name: str = Field(
        default="provider-gateway", validation_alias="PROVIDER_GATEWAY_SERVICE_NAME"
    )
    browser_enabled: bool = Field(default=True, validation_alias="PROVIDER_GATEWAY_BROWSER_ENABLED")
    browser_command: str = Field(
        default="playwright-mcp --headless --isolated",
        validation_alias="PROVIDER_GATEWAY_BROWSER_COMMAND",
    )
    upstream_timeout_seconds: int = Field(
        default=60, ge=5, le=300, validation_alias="PROVIDER_GATEWAY_UPSTREAM_TIMEOUT_SECONDS"
    )

    smtp_host: str | None = Field(default=None, validation_alias="SMTP_HOST")
    smtp_port: int = Field(default=587, validation_alias="SMTP_PORT")
    smtp_user: str | None = Field(default=None, validation_alias="SMTP_USER")
    smtp_password: str | None = Field(default=None, validation_alias="SMTP_PASSWORD")
    smtp_starttls: bool = Field(default=True, validation_alias="SMTP_STARTTLS")
    imap_host: str | None = Field(default=None, validation_alias="IMAP_HOST")
    email_from: str | None = Field(default=None, validation_alias="EMAIL_FROM")

    caldav_url: str | None = Field(default=None, validation_alias="CALDAV_URL")
    caldav_user: str | None = Field(default=None, validation_alias="CALDAV_USER")
    caldav_password: str | None = Field(default=None, validation_alias="CALDAV_PASSWORD")
    caldav_calendar: str | None = Field(default=None, validation_alias="CALDAV_CALENDAR")
    gemini_api_key: str | None = Field(
        default=None, validation_alias="PROVIDER_GATEWAY_GEMINI_API_KEY"
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

    def public_keyring(self) -> dict[str, str]:
        keys = dict(self.authority_public_keys)
        if self.authority_public_keys_file:
            try:
                raw = json.loads(Path(self.authority_public_keys_file).read_text())
            except OSError as exc:
                raise ValueError(
                    "Provider Gateway authority keyring file could not be read"
                ) from exc
            if not isinstance(raw, dict) or not all(
                isinstance(key, str) and isinstance(value, str) for key, value in raw.items()
            ):
                raise ValueError("Provider Gateway authority keyring file must contain a JSON map")
            keys.update(raw)
        if public := self.public_key_pem():
            keys[authority_key_id(public)] = public
        if keys:
            authority_public_keyring(keys)
        return keys

    def resolved_egress_proxy_url(self) -> str | None:
        if self.egress_proxy_url:
            return self.egress_proxy_url
        if self.egress_proxy_service_host:
            return f"http://{self.egress_proxy_service_host}:{self.egress_proxy_service_port}"
        return None

    @property
    def email_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_password)

    @property
    def calendar_configured(self) -> bool:
        return bool(self.caldav_url and self.caldav_user and self.caldav_password)
