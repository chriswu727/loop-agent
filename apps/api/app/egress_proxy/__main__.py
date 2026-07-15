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
    redis_url = settings.resolved_state_redis_url()
    revocation_path = settings.revocation_database_path or settings.audit_database_path
    revocations = AuthorityRevocationStore(
        revocation_path,
        redis_url=redis_url,
        namespace=f"{settings.state_namespace}:authority",
    )
    audit = AuditStore(
        settings.audit_database_path,
        max_events_per_run=settings.audit_max_events_per_run,
        max_events_total=settings.audit_max_events_total,
        redis_url=redis_url,
        namespace=settings.state_namespace,
    )
    if settings.require_authority_key and not keys:
        raise RuntimeError("Egress proxy requires an Ed25519 authority verification key")
    if settings.require_durable_audit and not audit.durable:
        raise RuntimeError("Egress proxy requires a durable audit database")
    if settings.require_durable_revocations and not revocations.durable:
        raise RuntimeError("Egress proxy requires durable authority revocations")
    if settings.require_shared_state and not (audit.shared and revocations.shared):
        raise RuntimeError("Egress proxy requires shared enforcement state")
    for key in keys.values():
        validate_authority_public_key(key)
    proxy = EgressProxy(settings, audit, revocations=revocations)
    try:
        await audit.ready()
        await revocations.ready()
        await revocations.subscribe(proxy.revoke)
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
            tasks = {
                asyncio.create_task(server.serve_forever()),
                asyncio.create_task(admin.serve()),
            }
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()
    finally:
        await revocations.close()
        await audit.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
