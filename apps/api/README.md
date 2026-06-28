# api — FastAPI backend

Layered, async, production-shaped FastAPI service. **No business logic** — one
example `items` resource shows the conventions end-to-end.

## Layout

```
app/
  core/          config, logging, security, lifespan
  api/           transport: errors, v1 router, deps, routes
  services/      use-cases (your business logic goes here)
  repositories/  data access (BaseRepository + concrete repos)
  domain/        pure entities
  schemas/       Pydantic DTOs (the API contract)
  db/            async engine/session + ORM models
  cache/         Cache protocol + Redis impl + in-memory fake
  middleware/    request-id / access logging
  observability/ tracing (OTel) + metrics (Prometheus)
  workers/       background job queue + consumer
alembic/         migrations
tests/           pytest (runs on in-memory sqlite, no services needed)
```

## Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload
```

- Swagger UI: http://localhost:8000/docs
- OpenAPI JSON: http://localhost:8000/openapi.json
- Liveness/readiness: `/healthz`, `/readyz`
- Metrics: `/metrics`

## Quality gate

```bash
ruff check . && ruff format --check .
mypy app
pytest
```

## The dependency rule

`api → services → repositories → domain`. Arrows point inward only. A router
never touches the DB; a repository never imports FastAPI. See the root
`ARCHITECTURE.md`.
