# Deployment

## 1. Build & push images

CI does this automatically (`.github/workflows/docker.yml`) on push to `main`
and on `v*` tags, publishing to GHCR. Manually:

```bash
docker build -f infra/docker/api.Dockerfile -t ghcr.io/your-org/app-api:1.0.0 --target runtime .
docker build -f infra/docker/web.Dockerfile -t ghcr.io/your-org/app-web:1.0.0 --target runner .
docker push ghcr.io/your-org/app-api:1.0.0
docker push ghcr.io/your-org/app-web:1.0.0
```

## 2. Point the overlay at your images

Edit `infra/k8s/overlays/<env>/kustomization.yaml` → `images:` (newName/newTag).
Prefer pinning to an immutable digest in production.

## 3. Provide real secrets

The base ships an **example** Secret. Replace it with a real one via Sealed
Secrets, External Secrets Operator, or your cloud's secret manager, and remove
`secret.example.yaml` from the base `resources`.

## 4. Apply

```bash
kubectl kustomize infra/k8s/overlays/prod | less   # review the diff first
kubectl apply -k infra/k8s/overlays/prod
```

## 5. Migrations

Run Alembic as a one-shot Job (or an init container) before the new pods take
traffic. A minimal Job:

```bash
kubectl run migrate --rm -it --restart=Never \
  --image=ghcr.io/your-org/app-api:1.0.0 \
  --env-from=secret/app-secrets -- alembic upgrade head
```

(Promote this into a proper `Job` manifest with `envFrom` for real pipelines.)

## Rollout & rollback

- Deploys are `RollingUpdate` with `maxUnavailable: 0` — no dropped requests.
- Roll back: `kubectl rollout undo deployment/api -n scalable-starter-prod`.
