# Loop API

FastAPI control plane and worker runtime for a least-authority, receipt-producing
agent loop.

## Layout

```text
app/
  api/             authenticated HTTP/SSE routes and RFC 9457 errors
  services/        task loop, verifier, Receipt, scheduler, memory, approvals
  repositories/    async SQLAlchemy persistence and atomic claims
  domain/          capability and task contracts
  tools/           workspace jail, gateway/proxy clients, Docker/Kubernetes execution
  provider_gateway credential-isolated browser/email/calendar/vision runtime
  egress_proxy/    token-verifying, destination-enforcing forward proxy
  workers/         Redis Streams producer, leased consumer, retries, DLQ
  cli/             loop receipt inspect|verify|replay|evaluate
alembic/           versioned schema migrations
tests/             offline API, loop, security, queue, and Receipt tests
```

## Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,office]"
alembic upgrade head
uvicorn app.main:app --reload
```

Swagger is at `/docs`; liveness, readiness, and Prometheus metrics are `/healthz`,
`/readyz`, and `/metrics`.

Worker mode mints short-lived Ed25519 authority grants. The Provider Gateway and
egress proxy receive only the public verifier, independently enforce the grant, and
return per-run audit events that are stored in the task and Receipt. Network
capabilities require explicit destination hosts; empty never means unrestricted.

## Verify

```bash
ruff check . && ruff format --check .
mypy app
pytest
pip-audit
```

The dependency rule is `api → services → repositories → domain`. See the root
`ARCHITECTURE.md` for runtime, authority, queue, and failure semantics.
