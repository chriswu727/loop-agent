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
PEM only in `authority-issuer-secrets` for the worker. Put the public PEM in
`email-gateway-secrets`, `calendar-gateway-secrets`, `vision-gateway-secrets`,
`browser-gateway-secrets`, and `authority-verifier-secrets` for the four gateways
and egress proxy. Never give the issuer key to an enforcement service.

Rotate authority keys without invalidating in-flight runs in this order:

1. Add both old and new public PEMs, keyed by the `kid` printed by
   `make authority-keygen`, to `PROVIDER_GATEWAY_AUTHORITY_PUBLIC_KEYS` in all four
   gateway secrets and to `EGRESS_PROXY_AUTHORITY_PUBLIC_KEYS` as JSON maps; roll
   out all five verifiers.
2. Switch the worker's `AGENT_AUTHORITY_SIGNING_KEY` to the new private key.
3. Wait at least `AGENT_AUTHORITY_TOKEN_TTL_SECONDS` (maximum 15 minutes), then
   remove the old public key from every verifier keyring.

Put SMTP/IMAP credentials only in `email-gateway-secrets`, CalDAV credentials only
in `calendar-gateway-secrets`, and the provider-vision key only in
`vision-gateway-secrets`. `browser-gateway-secrets` contains only public verifier
keys. LLM credentials remain worker credentials. The example Secret file shows the
required object/key split; use an external secret manager in production rather than
applying that example. Set each `AGENT_*_EGRESS_HOSTS` ConfigMap value to the exact
upstream hostnames used by its gateway. Add a custom provider port to
`EGRESS_PROXY_ALLOWED_PORTS` only when the deployment genuinely needs it.
Set `AGENT_SANDBOX_IMAGE_DIGEST=sha256:...` in the production ConfigMap after
publishing the sandbox image; production rejects mutable tag-only execution.

Provision durable Redis before the Loop workloads. The base manifests expect a
ClusterIP Service named `redis`; its backing pods must carry
`app.kubernetes.io/name=redis` so the included NetworkPolicies admit only port 6379.
The Kubernetes service-link IP lets the DNS-disabled gateways reach Redis without a
general DNS channel. If your Redis is managed outside the namespace, supply an
explicit `*_STATE_REDIS_URL` and adapt the CNI egress policy to its fixed private
endpoint.

The egress proxy stores bounded audit events and run revocations in Redis. Redis
Pub/Sub distributes revocations so every proxy and protocol-gateway replica closes
the affected connections or sessions. Email, calendar, vision, and egress proxy are
therefore safe to roll and scale horizontally. Live Chromium processes remain local
to one Browser Gateway pod, so the base deliberately keeps that deployment at one
replica with `Recreate` until session-affinity or external browser-session routing is
added. Redis durability and HA are part of the production trust boundary; use AOF or
a managed replicated service and monitor persistence failures.

## 4. Apply

```bash
kubectl kustomize infra/k8s/overlays/prod | less   # review the diff first
kubectl apply -k infra/k8s/overlays/prod
```

After rollout, verify every runtime boundary and inspect a test task Receipt:

```bash
kubectl rollout status deployment/api -n loop-prod
kubectl rollout status deployment/worker -n loop-prod
kubectl rollout status deployment/email-gateway -n loop-prod
kubectl rollout status deployment/calendar-gateway -n loop-prod
kubectl rollout status deployment/vision-gateway -n loop-prod
kubectl rollout status deployment/browser-gateway -n loop-prod
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
