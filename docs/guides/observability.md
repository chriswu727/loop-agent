# Observability

Three correlated pillars, all keyed by **request id**.

## Logs

Structured via `structlog` — pretty console in dev, JSON in prod. Every line
carries `request_id` (set by `RequestContextMiddleware`). Set `LOG_FORMAT=json`
and ship stdout to your log backend.

## Traces

OpenTelemetry auto-instruments FastAPI, SQLAlchemy, and Redis. Set
`OTEL_EXPORTER_OTLP_ENDPOINT` (e.g. an OTel Collector) to enable; unset = no-op.
Spans inherit the service name from `OTEL_SERVICE_NAME`.

## Metrics

Prometheus `/metrics` exposes request rate, latency histograms, and error counts
(the RED method), labeled by route template. Pods are annotated for scraping
(`prometheus.io/scrape`). Build dashboards/alerts on:

- request rate & error ratio per route,
- p50/p95/p99 latency,
- saturation (CPU/memory vs. limits) to validate HPA targets.

## Health

- `/healthz` (liveness) — cheap, no dependencies.
- `/readyz` (readiness) — checks DB + cache; gates traffic.
