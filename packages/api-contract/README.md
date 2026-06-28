# @repo/api-contract

Shared TypeScript types describing the API contract, consumed by `web` (and any
other client). Keeping the contract in one package means the frontend can never
silently drift from the backend.

Two ways to keep it in sync:

1. **Hand-written** (`src/index.ts`) — fine for a small surface.
2. **Generated** — `pnpm --filter @repo/api-contract generate` runs
   `openapi-typescript` against the backend's live `/openapi.json` and writes
   `src/generated.ts`. Wire this into CI to fail builds when the contract drifts.
