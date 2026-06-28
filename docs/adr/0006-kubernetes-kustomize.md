# 0006 — Kubernetes + Kustomize for deployment

- Status: Accepted
- Date: 2026-01-01

## Context

The goal is platform-agnostic, horizontally scalable deployment without cloud
lock-in, that works the same on a laptop (kind/k3s) and in production (EKS/GKE/AKS).

## Decision

Package the app as containers and deploy with **Kubernetes**, configured via
**Kustomize** (a `base` plus per-environment `overlays`). No templating language.

## Consequences

- Same manifests everywhere; environments differ only by small overlays.
- Built-in autoscaling (HPA), self-healing, rolling deploys, and disruption budgets.
- Trade-off: Kubernetes has operational overhead. Teams that want zero ops can
  drop `infra/k8s` and run the same containers on a PaaS or serverless platform;
  the app is deliberately agnostic to where it runs.
