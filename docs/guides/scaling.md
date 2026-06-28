# Scaling

The architecture's core property is **stateless compute, stateful edges**. That
makes scaling mostly a matter of adding pods.

## Compute tiers (web / api / worker)

- Horizontal scaling via **HorizontalPodAutoscaler** (in the base). `api` scales
  on CPU + memory, 2→20 replicas (50 in prod). Tune the targets in the overlay.
- For latency- or throughput-based scaling, expose a custom metric (e.g. p95
  latency via prometheus-adapter) and add it to the HPA `metrics`.
- The **worker** ideally scales on **queue depth**. Install KEDA or
  prometheus-adapter and add an `External`/`Pods` metric (e.g. Redis list length)
  to `worker-hpa.yaml`. CPU scaling is the out-of-the-box placeholder.

## Database

The standard ladder, in order, only as needed:

1. **Connection pooling** — put PgBouncer in front of Postgres; async apps open
   many short-lived connections.
2. **Read replicas** — set `DATABASE_READ_URL` and route reads to it. The
   repository layer means services don't change.
3. **Partitioning / sharding** or a managed distributed Postgres for write
   scaling at the far end.

## Cache

Redis absorbs hot reads and rate-limit counters. Promote to a managed/clustered
Redis under pressure; the `Cache` interface keeps app code unchanged.

## Checklist when traffic grows

- [ ] HPA targets sane? (CPU ~70%, headroom for spikes)
- [ ] Requests/limits reflect real usage? (check `kubectl top pods`)
- [ ] DB connection count within pooler limits?
- [ ] Slow queries indexed? N+1s removed?
- [ ] Hot endpoints cached with a sensible TTL?
- [ ] Worker keeping up with queue depth?
