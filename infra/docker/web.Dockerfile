# syntax=docker/dockerfile:1.7
# =============================================================================
# Next.js frontend image. Uses Next "standalone" output for a tiny runtime.
# Multi-stage: `dev` (hot reload, used by compose) and non-root `runner` (prod).
# Build context is the REPO ROOT:  docker build -f infra/docker/web.Dockerfile .
# =============================================================================
FROM node:24-alpine AS base
RUN corepack enable
WORKDIR /app

# ---- deps: install the whole workspace from manifests (great layer caching) ----
FROM base AS deps
COPY package.json pnpm-workspace.yaml turbo.json .npmrc ./
COPY apps/web/package.json apps/web/package.json
COPY packages/eslint-config/package.json packages/eslint-config/package.json
COPY packages/tsconfig/package.json packages/tsconfig/package.json
COPY packages/api-contract/package.json packages/api-contract/package.json
# Once you commit a pnpm-lock.yaml, switch to: pnpm install --frozen-lockfile
RUN pnpm install

# ---- builder: build the standalone server ----
FROM base AS builder
COPY --from=deps /app/node_modules ./node_modules
COPY --from=deps /app/apps/web/node_modules ./apps/web/node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN pnpm --filter web build

# ---- dev: hot reload (source is bind-mounted by docker-compose) ----
FROM base AS dev
COPY --from=deps /app/node_modules ./node_modules
COPY --from=deps /app/apps/web/node_modules ./apps/web/node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
EXPOSE 3000
CMD ["pnpm", "--filter", "web", "dev"]

# ---- runner: minimal, non-root, production ----
FROM base AS runner
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
RUN addgroup -g 1001 nodejs && adduser -u 1001 -G nodejs -S nextjs
# Next standalone output preserves the monorepo structure under apps/web.
COPY --from=builder --chown=nextjs:nodejs /app/apps/web/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/apps/web/.next/static ./apps/web/.next/static
COPY --from=builder --chown=nextjs:nodejs /app/apps/web/public ./apps/web/public
USER nextjs
EXPOSE 3000
ENV PORT=3000 HOSTNAME=0.0.0.0
CMD ["node", "apps/web/server.js"]
