# 0007 — PostgreSQL + Redis as default datastores

- Status: Accepted
- Date: 2026-01-01

## Context

We need a dependable primary datastore and a fast cache/broker, both ubiquitous
and well-understood, available as managed services on every cloud.

## Decision

**PostgreSQL** as the system of record; **Redis** for caching, rate-limiting,
and a lightweight job queue. Both are hidden behind interfaces (repository,
`Cache` protocol) so they can be swapped.

## Consequences

- Boring, battle-tested, easy to hire for, managed everywhere.
- The repository/cache abstractions keep business code independent of these choices.
- Trade-off: at extreme scale you may outgrow a single Postgres primary; the
  read/write-split hook and standard scaling ladder (pooling → replicas →
  partitioning) cover the journey.
