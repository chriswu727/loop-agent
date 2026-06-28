# 0005 — Layered (ports & adapters) backend

- Status: Accepted
- Date: 2026-01-01

## Context

Single-file apps are fast to start and miserable to scale. We want a structure
that stays navigable from 1 to 100+ features.

## Decision

Layer the backend: **transport → service → repository → domain**, with
dependencies pointing inward only. Schemas (Pydantic) are the wire contract;
the domain is framework-free.

## Consequences

- Local reasoning, targeted tests, swappable infrastructure (e.g. change the DB
  by rewriting one layer).
- A consistent place for every kind of code, so features are mechanical to add.
- Trade-off: more files/indirection than a single module — worth it past the
  smallest prototype, which is exactly this skeleton's audience.
