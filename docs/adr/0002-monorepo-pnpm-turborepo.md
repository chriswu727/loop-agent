# 0002 — Monorepo with pnpm + Turborepo

- Status: Accepted
- Date: 2026-01-01

## Context

Frontend, backend, infra, and shared types evolve together. Splitting them
across repos creates version-skew, duplicated CI, and painful cross-cutting
changes.

## Decision

One monorepo. JS/TS workspaces via **pnpm**; task orchestration/caching via
**Turborepo**. Python backend lives in the same repo but manages its own deps
with `pyproject.toml` (it is not a pnpm workspace member).

## Consequences

- Atomic cross-stack changes and a single source of truth for the API contract.
- Fast, cached builds (`turbo`) and strict, disk-efficient installs (`pnpm`).
- One CI pipeline, one `make` entrypoint.
- Trade-off: a polyglot repo needs both Node and Python toolchains present.
