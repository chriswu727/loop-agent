from __future__ import annotations

import asyncio

import uvicorn

from app.domain.authority_revocation import AuthorityRevocationStore
from app.domain.authority_token import validate_authority_public_key
from app.egress_proxy.admin import create_admin_app
from app.egress_proxy.audit import AuditStore
from app.egress_proxy.config import EgressProxySettings
from app.egress_proxy.proxy import EgressProxy


async def run() -> None:
    settings = EgressProxySettings()
    keys = settings.public_keyring()
    if settings.require_authority_key and not keys:
        raise RuntimeError("Egress proxy requires an Ed25519 authority verification key")
    if settings.require_durable_audit and not settings.audit_database_path:
        raise RuntimeError("Egress proxy requires a durable audit database")
    revocation_path = settings.revocation_database_path or settings.audit_database_path
    revocations = AuthorityRevocationStore(revocation_path)
    if settings.require_durable_revocations and not revocations.durable:
        raise RuntimeError("Egress proxy requires durable authority revocations")
    for key in keys.values():
        validate_authority_public_key(key)
    audit = AuditStore(
        settings.audit_database_path,
        max_events_per_run=settings.audit_max_events_per_run,
        max_events_total=settings.audit_max_events_total,
    )
    proxy = EgressProxy(settings, audit, revocations=revocations)
    server = await asyncio.start_server(proxy.handle, settings.host, settings.port)
    admin = uvicorn.Server(
        uvicorn.Config(
            create_admin_app(settings, audit, revocations, proxy.revoke),
            host=settings.admin_host,
            port=settings.admin_port,
            log_level="info",
        )
    )
    async with server:
        await asyncio.gather(server.serve_forever(), admin.serve())


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
