#!/usr/bin/env bash
set -euo pipefail

namespace="${1:-loop-prod}"
probe="loop-enforcement-smoke-$$"
deployments=(api web worker email-gateway calendar-gateway vision-gateway browser-gateway egress-proxy)

cleanup() {
  kubectl delete pod "$probe" --namespace "$namespace" --ignore-not-found --wait=false >/dev/null 2>&1 || true
}

trap cleanup EXIT
for deployment in "${deployments[@]}"; do
  kubectl rollout status "deployment/$deployment" --namespace "$namespace" --timeout=5m
done

proxy_ip="$(kubectl get service egress-proxy --namespace "$namespace" -o jsonpath='{.spec.clusterIP}')"
redis_ip="$(kubectl get service redis --namespace "$namespace" -o jsonpath='{.spec.clusterIP}')"
if [[ -z "$proxy_ip" || "$proxy_ip" == "None" || -z "$redis_ip" || "$redis_ip" == "None" ]]; then
  echo "egress-proxy and redis must be ClusterIP services" >&2
  exit 1
fi

kubectl run "$probe" \
  --namespace "$namespace" \
  --image python:3.12-alpine \
  --restart Never \
  --labels 'app.kubernetes.io/component=provider,app.kubernetes.io/name=enforcement-smoke' \
  --env "PROXY_IP=$proxy_ip" \
  --env "REDIS_IP=$redis_ip" \
  --command -- python -c '
import os
import socket

def connect(host, port):
    with socket.create_connection((host, port), timeout=5):
        pass

connect(os.environ["PROXY_IP"], 8080)
connect(os.environ["REDIS_IP"], 6379)
try:
    connect("1.1.1.1", 443)
except OSError:
    print("proxy and Redis reachable; direct public egress denied")
else:
    raise SystemExit("direct public egress unexpectedly succeeded")
'

if ! kubectl wait \
  --namespace "$namespace" \
  --for=jsonpath='{.status.phase}'=Succeeded \
  "pod/$probe" \
  --timeout=90s; then
  kubectl logs "$probe" --namespace "$namespace" || true
  kubectl describe pod "$probe" --namespace "$namespace" || true
  exit 1
fi
kubectl logs "$probe" --namespace "$namespace"
