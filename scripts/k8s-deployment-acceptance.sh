#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cluster="${LOOP_ACCEPTANCE_CLUSTER:-la-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-0}-$$}"
namespace="loop-acceptance"
ingress_namespace="ingress-nginx"
overlay="$root/infra/k8s/overlays/acceptance"
placeholder="sha256:0000000000000000000000000000000000000000000000000000000000000000"
image_placeholder="registry.invalid/loop-sandbox:acceptance"
registry="${cluster}-registry"
registry_container="$registry"
tmp="$(mktemp -d)"
cluster_created=false
cluster_ready=false

diagnostics() {
  kubectl get pods --all-namespaces --output wide || true
  kubectl get events --all-namespaces --sort-by=.lastTimestamp | tail -100 || true
  while IFS= read -r pod; do
    kubectl logs "$pod" --namespace "$namespace" --all-containers --tail=200 || true
  done < <(kubectl get pods --namespace "$namespace" --output name 2>/dev/null || true)
}

cleanup() {
  local status=$?
  trap - EXIT
  set +e
  if [[ "$status" -ne 0 && "$cluster_ready" == "true" ]]; then
    diagnostics
  fi
  if [[ "$cluster_created" == "true" ]]; then
    k3d cluster delete "$cluster" >/dev/null 2>&1
    k3d registry delete "$registry" >/dev/null 2>&1
  fi
  rm -rf "$tmp"
  exit "$status"
}

build_image() {
  local image="$1"
  local dockerfile="$2"
  local target="$3"
  local scope="$4"
  local -a args=(buildx build --load --progress plain --tag "$image" --file "$dockerfile")
  if [[ -n "$target" ]]; then
    args+=(--target "$target")
  fi
  if [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
    args+=(
      --cache-from "type=gha,scope=$scope"
      --cache-to "type=gha,mode=max,scope=$scope"
    )
  fi
  docker "${args[@]}" "$root"
}

create_secrets() {
  openssl genpkey -algorithm ED25519 -out "$tmp/authority-private.pem"
  openssl pkey -in "$tmp/authority-private.pem" -pubout -out "$tmp/authority-public.pem"
  openssl genpkey -algorithm ED25519 -out "$tmp/receipt-private.pem"

  kubectl create secret generic app-secrets \
    --namespace "$namespace" \
    --from-literal SECRET_KEY=acceptance-session-secret-7c947cb5e192e209267ced87 \
    --from-literal DATABASE_URL=postgresql+asyncpg://app:app@postgres:5432/app \
    --from-literal API_TOKEN=acceptance-api-token-3a14d37f54824db7 \
    --from-file "AGENT_RECEIPT_SIGNING_KEY=$tmp/receipt-private.pem" \
    --dry-run=client --output yaml | kubectl apply -f -
  kubectl create secret generic web-secrets \
    --namespace "$namespace" \
    --from-literal LOOP_SESSION_SECRET=acceptance-session-secret-7c947cb5e192e209267ced87 \
    --from-literal GITHUB_CLIENT_ID=acceptance-client \
    --from-literal GITHUB_CLIENT_SECRET=acceptance-client-secret \
    --dry-run=client --output yaml | kubectl apply -f -
  kubectl create secret generic authority-issuer-secrets \
    --namespace "$namespace" \
    --from-file "AGENT_AUTHORITY_SIGNING_KEY=$tmp/authority-private.pem" \
    --dry-run=client --output yaml | kubectl apply -f -
  kubectl create secret generic authority-verifier-secrets \
    --namespace "$namespace" \
    --from-file "EGRESS_PROXY_AUTHORITY_PUBLIC_KEY=$tmp/authority-public.pem" \
    --dry-run=client --output yaml | kubectl apply -f -
  kubectl create secret generic browser-gateway-secrets \
    --namespace "$namespace" \
    --from-file "PROVIDER_GATEWAY_AUTHORITY_PUBLIC_KEY=$tmp/authority-public.pem" \
    --dry-run=client --output yaml | kubectl apply -f -
  kubectl create secret generic email-gateway-secrets \
    --namespace "$namespace" \
    --from-file "PROVIDER_GATEWAY_AUTHORITY_PUBLIC_KEY=$tmp/authority-public.pem" \
    --from-literal SMTP_HOST=smtp.example.com \
    --from-literal SMTP_PORT=587 \
    --from-literal SMTP_USER=acceptance \
    --from-literal SMTP_PASSWORD=acceptance \
    --from-literal IMAP_HOST=imap.example.com \
    --from-literal IMAP_PORT=993 \
    --from-literal EMAIL_FROM=acceptance@example.com \
    --dry-run=client --output yaml | kubectl apply -f -
  kubectl create secret generic calendar-gateway-secrets \
    --namespace "$namespace" \
    --from-file "PROVIDER_GATEWAY_AUTHORITY_PUBLIC_KEY=$tmp/authority-public.pem" \
    --from-literal CALDAV_URL=https://caldav.example.com/ \
    --from-literal CALDAV_USER=acceptance \
    --from-literal CALDAV_PASSWORD=acceptance \
    --dry-run=client --output yaml | kubectl apply -f -
  kubectl create secret generic vision-gateway-secrets \
    --namespace "$namespace" \
    --from-file "PROVIDER_GATEWAY_AUTHORITY_PUBLIC_KEY=$tmp/authority-public.pem" \
    --from-literal PROVIDER_GATEWAY_GEMINI_API_KEY=acceptance \
    --dry-run=client --output yaml | kubectl apply -f -
}

wait_for_succeeded_pod() {
  local pod="$1"
  local pod_namespace="$2"
  local timeout_seconds="$3"
  local deadline=$((SECONDS + timeout_seconds))
  local phase
  while ((SECONDS < deadline)); do
    phase="$(
      kubectl get pod "$pod" --namespace "$pod_namespace" \
        -o jsonpath='{.status.phase}' 2>/dev/null || true
    )"
    case "$phase" in
      Succeeded) return 0 ;;
      Failed) return 1 ;;
    esac
    sleep 1
  done
  return 1
}

run_cluster_probe() {
  local suffix="$1"
  local run_task="$2"
  local probe="loop-application-smoke-${suffix}-$$"
  local api_ip
  local web_ip
  api_ip="$(kubectl get service api --namespace "$namespace" -o jsonpath='{.spec.clusterIP}')"
  web_ip="$(kubectl get service web --namespace "$namespace" -o jsonpath='{.spec.clusterIP}')"

  kubectl run "$probe" \
    --namespace "$ingress_namespace" \
    --image python:3.12-alpine \
    --restart Never \
    --env "API_URL=http://${api_ip}:8000" \
    --env "WEB_URL=http://${web_ip}:3000" \
    --env API_TOKEN=acceptance-api-token-3a14d37f54824db7 \
    --env "SANDBOX_DIGEST=$sandbox_digest" \
    --env "RUN_TASK=$run_task" \
    --command -- python -c '
import json
import os
import time
import urllib.request

api = os.environ["API_URL"]
web = os.environ["WEB_URL"]
headers = {"Authorization": "Bearer " + os.environ["API_TOKEN"]}

def request(method, url, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    current = dict(headers)
    if body is not None:
        current["Content-Type"] = "application/json"
    with urllib.request.urlopen(
        urllib.request.Request(url, data=body, headers=current, method=method), timeout=10
    ) as response:
        return json.load(response)

ready = request("GET", api + "/readyz")
if ready.get("status") != "ready":
    raise SystemExit(f"API is not ready: {ready}")
with urllib.request.urlopen(web + "/api/health", timeout=10) as response:
    if response.status != 200:
        raise SystemExit(f"Web health returned {response.status}")

if os.environ["RUN_TASK"] == "true":
    task = request(
        "POST",
        api + "/api/v1/tasks",
        {
            "goal": "Write and verify the deterministic Fibonacci demo",
            "limits": {"max_steps": 8, "token_budget": 10000},
        },
    )
    deadline = time.monotonic() + 240
    while task["status"] not in {"completed", "failed", "cancelled"}:
        if time.monotonic() >= deadline:
            raise SystemExit(f"Task timed out: {task}")
        time.sleep(1)
        task = request("GET", api + "/api/v1/tasks/" + task["id"])
    if task["status"] != "completed":
        raise SystemExit(f"Task did not complete: {task}")
    if task["sandbox"] != "kubernetes" or task["verified_by"] != "execution":
        steps = request("GET", api + "/api/v1/tasks/" + task["id"] + "/steps")
        raise SystemExit(
            f"Task bypassed Kubernetes execution verification: task={task}, steps={steps}"
        )
    report = request("GET", api + "/api/v1/tasks/" + task["id"] + "/receipt")
    if not report.get("valid") or not report.get("authentic"):
        raise SystemExit(f"Receipt is not authentic: {report}")
    provenance = report["receipt"]["provenance"]["sandbox"]
    if provenance["image_digest"] != os.environ["SANDBOX_DIGEST"]:
        raise SystemExit(f"Receipt recorded the wrong sandbox digest: {provenance}")
    print(json.dumps({
        "receipt_authentic": True,
        "sandbox": task["sandbox"],
        "status": task["status"],
        "task_id": task["id"],
        "verified_by": task["verified_by"],
    }, sort_keys=True))
else:
    print(json.dumps({"api": "ready", "web": "ready"}, sort_keys=True))
'

  if ! wait_for_succeeded_pod "$probe" "$ingress_namespace" 300; then
    kubectl logs "$probe" --namespace "$ingress_namespace" || true
    kubectl describe pod "$probe" --namespace "$ingress_namespace" || true
    return 1
  fi
  kubectl logs "$probe" --namespace "$ingress_namespace"
  kubectl delete pod "$probe" --namespace "$ingress_namespace" --wait=false >/dev/null
}

for command in docker k3d kubectl openssl python3; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "$command is required" >&2
    exit 1
  fi
done

trap cleanup EXIT

build_image loop-api:acceptance "$root/infra/docker/api.Dockerfile" runtime k8s-api
build_image loop-provider-gateway:acceptance "$root/infra/docker/api.Dockerfile" provider-gateway k8s-api
build_image loop-web:acceptance "$root/infra/docker/web.Dockerfile" runner k8s-web
build_image loop-sandbox:acceptance "$root/apps/api/sandbox.Dockerfile" '' k8s-sandbox
if [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
  docker buildx stop >/dev/null 2>&1 || true
fi

cluster_created=true
k3d cluster create "$cluster" \
  --image rancher/k3s:v1.36.2-k3s1 \
  --servers 1 \
  --agents 0 \
  --no-lb \
  --wait \
  --timeout 180s \
  --registry-create "$registry" \
  --k3s-arg '--disable=traefik@server:*' \
  --k3s-arg '--disable=servicelb@server:*'
cluster_ready=true
k3d image import --cluster "$cluster" \
  loop-api:acceptance \
  loop-provider-gateway:acceptance \
  loop-web:acceptance

registry_port="$(
  docker inspect \
    --format '{{(index (index .NetworkSettings.Ports "5000/tcp") 0).HostPort}}' \
    "$registry_container"
)"
if [[ ! "$registry_port" =~ ^[0-9]+$ ]]; then
  echo "Could not resolve the acceptance registry port: $registry_port" >&2
  exit 1
fi
sandbox_push_image="127.0.0.1:${registry_port}/loop-sandbox:acceptance"
sandbox_image="${registry_container}:5000/loop-sandbox:acceptance"
docker tag loop-sandbox:acceptance "$sandbox_push_image"
push_output="$(docker push "$sandbox_push_image")"
printf '%s\n' "$push_output"
sandbox_digest="$(
  printf '%s\n' "$push_output" |
    awk '$1 == "acceptance:" && $2 == "digest:" {digest = $3} END {print digest}'
)"
if [[ ! "$sandbox_digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "Could not resolve the pushed sandbox manifest digest: $sandbox_digest" >&2
  exit 1
fi

kubectl create namespace "$namespace"
kubectl label namespace "$namespace" \
  app.kubernetes.io/part-of=loop \
  app.kubernetes.io/managed-by=kustomize \
  --overwrite
kubectl create namespace "$ingress_namespace"

create_secrets
kubectl apply --namespace "$namespace" -f "$overlay/dependencies.yaml"
kubectl rollout status deployment/postgres --namespace "$namespace" --timeout=3m
kubectl rollout status deployment/redis --namespace "$namespace" --timeout=3m

kubectl apply --namespace "$namespace" -f "$root/infra/k8s/base/configmap.yaml"
kubectl patch configmap app-config \
  --namespace "$namespace" \
  --type merge \
  --patch "{\"data\":{\"AGENT_SANDBOX_IMAGE\":\"$sandbox_image\",\"AGENT_SANDBOX_IMAGE_DIGEST\":\"$sandbox_digest\",\"APP_BASE_URL\":\"http://web\",\"CORS_ORIGINS\":\"http://web\",\"DEMO_MODE\":\"true\",\"LLM_DEFAULT_PROVIDER\":\"mock\",\"PROMETHEUS_ENABLED\":\"false\"}}"

kubectl apply --namespace "$namespace" -f "$overlay/migration-job.yaml"
if ! kubectl wait \
  --namespace "$namespace" \
  --for=condition=complete \
  job/migration \
  --timeout=3m; then
  kubectl logs job/migration --namespace "$namespace" || true
  exit 1
fi
kubectl logs job/migration --namespace "$namespace"
migration="$(
  kubectl exec deployment/postgres --namespace "$namespace" -- \
    psql -U app -d app -tAc 'SELECT version_num FROM alembic_version'
)"
if [[ "$migration" != "0008_verified_completion_contract" ]]; then
  echo "Unexpected Alembic revision: $migration" >&2
  exit 1
fi

kubectl kustomize "$overlay" >"$tmp/acceptance.raw.yaml"
if [[ "$(grep -Fc "$placeholder" "$tmp/acceptance.raw.yaml")" -ne 1 ]]; then
  echo "Acceptance manifest must contain exactly one sandbox digest placeholder" >&2
  exit 1
fi
if [[ "$(grep -Fc "$image_placeholder" "$tmp/acceptance.raw.yaml")" -ne 1 ]]; then
  echo "Acceptance manifest must contain exactly one sandbox image placeholder" >&2
  exit 1
fi
sed -e "s|$placeholder|$sandbox_digest|" \
  -e "s|$image_placeholder|$sandbox_image|" \
  "$tmp/acceptance.raw.yaml" >"$tmp/acceptance.yaml"
kubectl apply -f "$tmp/acceptance.yaml"

bash "$root/scripts/k8s-enforcement-smoke.sh" "$namespace"
run_cluster_probe before-rollback true

stable_image="$(
  kubectl get deployment api --namespace "$namespace" \
    -o jsonpath='{.spec.template.spec.containers[?(@.name=="api")].image}'
)"
kubectl set image deployment/api \
  --namespace "$namespace" \
  api=loop-api:rollback-must-fail
if kubectl rollout status deployment/api --namespace "$namespace" --timeout=30s; then
  echo "Broken API image unexpectedly rolled out" >&2
  exit 1
fi
kubectl rollout undo deployment/api --namespace "$namespace"
kubectl rollout status deployment/api --namespace "$namespace" --timeout=3m
restored_image="$(
  kubectl get deployment api --namespace "$namespace" \
    -o jsonpath='{.spec.template.spec.containers[?(@.name=="api")].image}'
)"
if [[ "$restored_image" != "$stable_image" ]]; then
  echo "Rollback restored $restored_image instead of $stable_image" >&2
  exit 1
fi
run_cluster_probe after-rollback false

printf '{"migration":"%s","rollback":true,"sandbox_digest":"%s","status":"passed"}\n' \
  "$migration" "$sandbox_digest"
