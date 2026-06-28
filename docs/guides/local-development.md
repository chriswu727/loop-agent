# Local development

Two ways to run the stack.

## Docker (recommended)

```bash
cp .env.example .env
make up        # web + api + postgres + redis, with hot reload
```

- Web: http://localhost:3000
- API docs: http://localhost:8000/docs
- Stop: `make down` (add data wipe with `make clean-volumes`)

## Native (fastest iteration)

Requires Node 20+, pnpm 10+, Python 3.12+.

```bash
make setup     # installs JS + Python deps, creates .env
# start Postgres + Redis however you like, e.g.:
docker compose up -d postgres redis
make migrate   # apply migrations
make dev       # web + api in watch mode
```

## Common tasks

| Task | Command |
|------|---------|
| All quality checks (CI parity) | `make check` |
| Format everything | `make format` |
| New migration | `make migration m="describe change"` |
| Apply migrations | `make migrate` |
| Backend tests only | `cd apps/api && pytest` |
| See all commands | `make help` |
