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

The base references `app-secrets` and `web-secrets` but deliberately does not
create them. Use `infra/k8s/base/secret.example.yaml` only as a field template,
then provide the real objects through Sealed Secrets, External Secrets Operator,
or your cloud secret manager. `SECRET_KEY` and `LOOP_SESSION_SECRET` must contain
the same random value. `app-secrets` must also provide a valid unencrypted Ed25519
PEM in `AGENT_RECEIPT_SIGNING_KEY`; generate one with `make receipt-keygen`.
Set `AGENT_SANDBOX_IMAGE_DIGEST=sha256:...` in the production ConfigMap after
publishing the sandbox image; production rejects mutable tag-only execution.

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
- Roll back: `kubectl rollout undo deployment/api -n loop-prod`.
