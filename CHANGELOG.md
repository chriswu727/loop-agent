# Changelog

All notable changes are documented here. The project follows Semantic Versioning.

## [0.1.0] - 2026-07-15

First portfolio/research release of Loop's contract-first autonomous runtime.

### Core

- ReAct execution with user-confirmed acceptance contracts, baseline regression
  discovery, independent verification, criterion-to-evidence coverage, and replayable
  `loop.receipt/v1` Receipts.
- Server-enforced capabilities, step/token budgets, verification reserves,
  no-progress detection, repeated-action blocking, and bounded sub-agents.
- Hash-chained step ledger, Ed25519 Receipt/skill signing, offline verification, and a
  reusable GitHub Receipt action.

### Isolation and durability

- Inline, Docker, and Kubernetes Job command backends with explicit isolation labels.
- Destination-bound egress proxy, audience-scoped authority tokens, revocation, and
  isolated browser/email/calendar/vision gateways.
- Redis Streams workers with leases, cross-worker reclaim, retries, DLQ, idempotent
  task claims, and restart acceptance coverage.
- Verified local Git change sets with Apply, Discard, conflict-safe Undo, and Receipt
  binding.

### Product surfaces

- Next.js task/chat/trigger UI, SSE progress with polling fallback, file and document
  workflows, memory, signed skills, GitHub OAuth, Telegram/Slack, and schedules.
- Electron desktop shell packaged and startup-smoked on macOS, Windows, and Linux.
- Opt-in local Sibyl research and Argus QA MCP surfaces under typed capabilities.

### Release verification

- One-command, zero-key demo with an ephemeral local API token and a deterministic
  strict-contract task.
- Playwright browser acceptance from task publication through Receipt replay.
- Published 12-case real-provider evaluation manifest plus a committed, explicitly
  non-model-quality deterministic smoke result.
- Disposable Kubernetes acceptance covering migration, task execution, Receipt
  authenticity, NetworkPolicy enforcement, failed rollout, and rollback.

[0.1.0]: https://github.com/chriswu727/loop-agent/releases/tag/v0.1.0
