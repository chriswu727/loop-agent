# infra/docker

Multi-stage, production-shaped images. Both build from the **repo root** as
context so they can see the whole monorepo.

| Image | Dockerfile | Targets |
|-------|------------|---------|
| API   | `api.Dockerfile` | `dev` (reload), `runtime` (non-root) |
| Web   | `web.Dockerfile` | `dev` (reload), `runner` (standalone, non-root) |

```bash
# Production images
docker build -f infra/docker/api.Dockerfile -t myreg/app-api:$(git rev-parse --short HEAD) .
docker build -f infra/docker/web.Dockerfile -t myreg/app-web:$(git rev-parse --short HEAD) .
```

`docker-compose.yml` builds the `dev` targets and bind-mounts source for hot
reload. Both runtime images run as a non-root user.
