# web — Next.js frontend

App Router + React 19 + TypeScript (strict) + Tailwind v4. **No product
features** — the landing page just pings the API and describes the skeleton.

## Layout

```
app/            routes, layouts, route handlers (App Router)
  api/health/   liveness endpoint for the k8s probe
components/     presentational components
lib/
  env.ts        validated, typed environment access
  api-client.ts single typed fetch client (+ example items client)
  utils.ts
```

## Run

```bash
pnpm install        # from the repo root (workspace)
pnpm --filter web dev
```

Reads `NEXT_PUBLIC_API_URL` (browser) and `API_INTERNAL_URL` (server) — see the
root `.env.example`. Server components fetch the API over the internal URL; the
browser uses the public one (`lib/env.ts#apiBaseUrl`).

## Conventions

- Server Components by default; add `'use client'` only where you need interactivity.
- All API calls go through `lib/api-client.ts`. Don't scatter `fetch` calls.
- New public env vars must be added to `lib/env.ts` and `.env.example`.
