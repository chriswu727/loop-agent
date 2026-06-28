# 0004 — Next.js frontend

- Status: Accepted
- Date: 2026-01-01

## Context

We want SSR/streaming, a first-class TypeScript experience, a large hiring pool,
and a smooth path from marketing pages to an app.

## Decision

**Next.js (App Router)** with React 19 and TypeScript in strict mode. Tailwind
for styling. A single typed API client; validated environment variables.

## Consequences

- Server Components reduce client JS; SSR improves first paint and SEO.
- `output: standalone` yields tiny container images.
- Trade-off: App Router has a learning curve; we keep client components minimal
  and push data fetching to the server.
