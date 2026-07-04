from __future__ import annotations

from httpx import AsyncClient


async def test_healthz(client: AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_readyz_checks_dependencies(client: AsyncClient) -> None:
    resp = await client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["cache"] == "ok"


async def test_readyz_returns_503_when_a_dependency_is_down(client: AsyncClient) -> None:
    # k8s reads the status code, not the body — a down dependency MUST be 503 so
    # the pod is pulled from rotation instead of serving errors.
    from app.api.v1.deps import get_cache
    from app.main import app

    class _BrokenCache:
        async def set(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("cache unreachable")

    app.dependency_overrides[get_cache] = lambda: _BrokenCache()
    try:
        resp = await client.get("/readyz")
    finally:
        app.dependency_overrides.pop(get_cache, None)

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["cache"] == "error"
    assert body["checks"]["database"] == "ok"  # the DB is still fine
