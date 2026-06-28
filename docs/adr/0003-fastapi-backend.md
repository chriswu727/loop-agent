# 0003 — FastAPI + Python backend

- Status: Accepted
- Date: 2026-01-01

## Context

The backend must be productive for small teams, async-capable for I/O-bound
workloads, and self-documenting. The user's team is fluent in Python.

## Decision

**FastAPI** on **Python 3.13**, with Pydantic v2 for validation and SQLAlchemy
2.0 (async) for persistence.

## Consequences

- Type-driven handlers, automatic OpenAPI docs, native async.
- Huge ecosystem and gentle onboarding.
- Trade-off: Python isn't the fastest runtime; we mitigate by staying async,
  keeping services stateless, and scaling horizontally. If a hot path needs more,
  it can be extracted into a separate service in any language — the layered
  boundaries make that local.
