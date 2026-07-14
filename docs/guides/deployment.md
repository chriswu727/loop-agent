# Deployment

## 1. Build & push images

CI builds every runtime on relevant pull requests without publishing. Version tags
and manual runs build and publish the images to GHCR. Manually:

```bash
docker build -f infra/docker/api.Dockerfile -t ghcr.io/your-org/app-api:1.0.0 --target runtime .
docker build -f infra/docker/api.Dockerfile -t ghcr.io/your-org/app-provider-gateway:1.0.0 --target provider-gateway .
docker build -f infra/docker/web.Dockerfile -t ghcr.io/your-org/app-web:1.0.0 --target runner .
docker push ghcr.io/your-org/app-api:1.0.0
docker push ghcr.io/your-org/app-provider-gateway:1.0.0
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
Generate the runtime authority pair with `make authority-keygen`. Put the private
PEM only in `authority-issuer-secrets` for the worker, and the public PEM in
`authority-verifier-secrets` for the Provider Gateway and egress proxy. Never give
the issuer key to either enforcement service.

Put SMTP/IMAP/CalDAV/provider-vision credentials only in
`provider-gateway-secrets`. LLM credentials remain worker credentials. The example
Secret file shows the required object/key split; use an external secret manager in
production rather than applying that example.
Set `AGENT_SANDBOX_IMAGE_DIGEST=sha256:...` in the production ConfigMap after
publishing the sandbox image; production rejects mutable tag-only execution.

The egress proxy keeps a bounded SQLite WAL on its dedicated `egress-proxy-audit`
PVC, while browser sessions live in gateway memory. Both base deployments therefore
use one replica: audit survives a proxy pod restart, but horizontal proxy or gateway
session HA still requires shared external stores.

## 4. Apply

```bash
kubectl kustomize infra/k8s/overlays/prod | less   # review the diff first
kubectl apply -k infra/k8s/overlays/prod
```

After rollout, verify every runtime boundary and inspect a test task Receipt:

```bash
kubectl rollout status deployment/api -n loop-prod
kubectl rollout status deployment/worker -n loop-prod
kubectl rollout status deployment/provider-gateway -n loop-prod
kubectl rollout status deployment/egress-proxy -n loop-prod
```

Run one task without network and one with `net.shell` plus a single disposable test
host. Confirm the first Job has no egress, the second cannot reach any undeclared
host, and its Receipt contains an allowed proxy audit event for the declared host.

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
