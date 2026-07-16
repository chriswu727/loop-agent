# Adding a resource

Use the closest existing vertical slice—tasks, triggers, or memory—as the reference.
The invariant is transport → service → repository → domain; business rules do not
belong in route handlers.

## Backend

1. Add a pure domain type under `apps/api/app/domain/` when the resource has behavior
   or states worth naming.
2. Add the SQLAlchemy model under `app/db/models/` and export it so Alembic sees it.
3. Add request/response schemas under `app/schemas/`.
4. Add a repository under `app/repositories/`; keep SQL and persistence mapping here.
5. Add a service under `app/services/`; enforce ownership, transitions, idempotency,
   and transaction rules here.
6. Add a dependency constructor in `app/api/v1/deps.py`, a thin router under
   `app/api/v1/routes/`, and register it in `app/api/v1/router.py`.
7. Generate and review a migration:

   ```bash
   make migration m="add projects"
   make migrate
   ```

8. Test service rules, ownership, HTTP serialization/errors, and migration/runtime
   boundaries. Use the in-memory SQLite/cache fixtures unless the behavior specifically
   requires Postgres or Redis.

## Frontend

1. Add the wire type to `packages/api-contract/src/index.ts`.
2. Extend the single typed client in `apps/web/lib/api-client.ts`.
3. Keep data loading in route/server components or the API client; leaf components
   should focus on rendering and interaction.
4. Add Vitest coverage for component behavior and Playwright coverage when the change
   affects the flagship publish → verify → Receipt journey.

## Cross-cutting checklist

- Schema changes include an Alembic migration.
- New environment variables appear in `.env.example`, typed settings, Compose, and the
  relevant Kubernetes manifests.
- New tools declare a capability and pass through `ToolExecutor.execute`.
- New side effects define approval, idempotency, audit, cancellation, and replay
  semantics before implementation.
- `make check` and the relevant acceptance job pass.
