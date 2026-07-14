# Loop web

Next.js App Router + React 19 + strict TypeScript + Tailwind v4.

## Responsibilities

- Publish tasks with explicit `loop.capabilities/v1` authority.
- Stream task progress, show resolved authority, inspect artifacts/ledger, and replay
  Receipt checks.
- Authenticate users through GitHub authorization code + PKCE.
- Keep API and session credentials server-side. Browser requests use the same-origin
  `/api/loop` route; its Node handler forwards only the user's HTTP-only JWT.

## Layout

```text
app/                  pages and server route handlers
  api/auth/github/    OAuth login and callback
  api/loop/           authenticated streaming API proxy
components/           product UI and component tests
lib/api-client.ts     typed client and problem-detail normalization
lib/env.ts            validated server/public environment contract
lib/session.ts        short-lived Loop JWT creation and verification
tests/                session and proxy security tests
```

## Run and verify

```bash
pnpm install
pnpm --filter web dev
pnpm --filter web test
pnpm --filter web lint
pnpm --filter web typecheck
pnpm --filter web build
```

`API_INTERNAL_URL` defaults to `http://localhost:8000`. For production GitHub login,
set `WEB_AUTH_REQUIRED=true`, `APP_BASE_URL`, GitHub client credentials, and
`LOOP_SESSION_SECRET` as documented in the root `.env.example`.
