# infra/k8s

Cloud-agnostic Kubernetes manifests with **Kustomize**. A single `base/` is
patched by per-environment `overlays/` — no copy-pasted YAML.

```
base/                 # shared manifests (the source of truth)
overlays/
  dev/                # 1 replica, debug logs, :dev images
  staging/            # 2 replicas, :staging images
  prod/               # 3+ replicas, tuned resources, HPA to 50, pinned images
```

## What's in the base

- **Deployments** for `api`, `web`, `worker`, the isolated email/calendar/vision/browser
  gateways, and the authenticated egress proxy — non-root, read-only rootfs,
  dropped capabilities, seccomp `RuntimeDefault`, resource requests/limits,
  liveness/readiness/startup probes, topology spread, graceful shutdown.
- **Services** (ClusterIP) for control-plane and enforcement traffic.
- **HorizontalPodAutoscalers** — CPU/memory targets; scale `api` 2→20.
- **PodDisruptionBudgets** — keep ≥1 replica during node drains.
- **Ingress** — TLS, `/api` → api, `/` → web.
- **NetworkPolicies** — default-deny ingress plus explicit worker-to-gateway and
  gateway-to-proxy/Redis allows. Provider and browser identities have no direct
  public egress.
- **ConfigMap / Secret** — config vs. secrets split (secret is an example; use a
  real secret manager in production).

## Use it

```bash
# Render (never blind-apply — read the diff first)
kubectl kustomize infra/k8s/overlays/prod | less

# Apply
kubectl apply -k infra/k8s/overlays/prod

# After rollout, prove proxy/Redis reachability and direct-egress denial.
make k8s-enforcement-smoke namespace=loop-prod
```

> Prefer Helm? The same topology maps 1:1 to a chart. Kustomize is the default
> here because it needs no templating language and no extra tooling.
