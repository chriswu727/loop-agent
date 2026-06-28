# Contributing

Thanks for building on Scalable Starter. This guide keeps the codebase coherent
as it grows.

## Getting started

```bash
make setup     # install JS + Python deps, create .env
make up        # or `make dev` for hot reload
```

## The one rule that matters

**Respect the layers.** Dependencies point inward only:
`api → services → repositories → domain`. A router must not touch the database;
a repository must not import FastAPI. See [`ARCHITECTURE.md`](./ARCHITECTURE.md).

## Before you open a PR

Run the same gate CI runs:

```bash
make check     # lint + typecheck + test (JS and Python)
```

- **Formatting:** Prettier (JS/TS) and Ruff (Python). `make format` fixes most things.
- **Types:** `tsc` and `mypy` must pass — no `any`, no untyped defs in new code.
- **Tests:** add/extend tests for behavior you change. Services are the easiest
  layer to unit-test (inject a fake repository).
- **Migrations:** any model change needs `make migration m="..."` committed alongside it.
- **Env vars:** new config goes in `.env.example`, `app/core/config.py`, and the
  relevant k8s ConfigMap/Secret in the same PR.

## Commit messages

Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`...). This
keeps history readable and enables automated changelogs later.

## Adding a feature

Follow [`docs/guides/adding-a-feature.md`](./docs/guides/adding-a-feature.md) and
use the `items` example as your template.
