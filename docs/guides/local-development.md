# Local development

Two ways to run the stack.

## Desktop (product path)

Requires Docker Desktop or Docker Engine with Compose.

```bash
pnpm install
pnpm --filter desktop dev
```

The first-run screen accepts a provider key and opens the native directory picker.
The selected directory must be the root of a clean Git repository. The desktop
supervisor builds and starts the production-shaped Compose overlay, mounts only that
repository into the API, and preserves private credentials and crash state below the
OS user-data directory. `pnpm --filter desktop make` creates the current platform's
installer; `pnpm --filter desktop smoke:packaged` launches the packaged binary with
an isolated temporary profile and verifies that its renderer sandbox starts.

## Docker (recommended)

```bash
cp .env.example .env
make up        # web + api + worker + gateways + postgres + redis
```

- Web: http://localhost:3000
- API docs: http://localhost:8000/docs
- Stop: `make down` (add data wipe with `make clean-volumes`)

## Native (fastest iteration)

Requires Node 22.13+, pnpm 11+, Python 3.12+.

```bash
make setup     # validates runtimes and installs JS + Python dependencies
# start Postgres + Redis however you like, e.g.:
docker compose up -d postgres redis
make migrate   # apply migrations
make dev       # API + web in watch mode with one temporary local token
```

To let Loop edit existing repositories through a reviewable change set, set one
local boundary in `.env` before starting the API:

```bash
LOOP_LOCAL_PROJECTS_ROOT=/absolute/path/to/your/projects
```

The publish form then accepts a repository path relative to that root. The source
checkout must be clean. Loop clones committed content into its task workspace,
removes the source remote, and does not expose either the source path or its own
absolute workspace path through the API. A successful task still cannot modify the
source until you review the diff and choose **Apply verified patch**. Apply leaves
normal uncommitted changes in the source repository; **Undo apply** reverses only
that exact patch and refuses on an overlap or changed base commit.

## Common tasks

| Task                           | Command                              |
| ------------------------------ | ------------------------------------ |
| All quality checks (CI parity) | `make check`                         |
| Format everything              | `make format`                        |
| New migration                  | `make migration m="describe change"` |
| Apply migrations               | `make migrate`                       |
| Backend tests only             | `cd apps/api && pytest`              |
| See all commands               | `make help`                          |
| Desktop app                    | `pnpm --filter desktop dev`          |
| Package desktop installer      | `pnpm --filter desktop make`         |
