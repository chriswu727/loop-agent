# Changelog

All notable changes are documented here. The project follows Semantic Versioning.

## [Unreleased]

### Core

- Added the one-instruction local-project path: bounded repository discovery, typed
  contract compilation, independent criticism, risk/confidence and authority gates,
  pre-mutation SHA-256 locking, clarification/resume, Receipt binding, and visible
  contract provenance in the task UI.
- Moved manual criteria, checks, artifacts, capabilities, and budgets into an Advanced
  panel while preserving them as immutable overrides when supplied.
- Hardened real-model contract compilation with schema normalization, minimal-contract
  prompting, bounded critic-driven repair with no-progress detection, and semantic
  deduplication of deterministic repository checks.
- Made discovered Python test gates use `python -m pytest -q` so flat repositories run
  consistently across virtual environments and pytest entrypoint modes.
- Added bounded source/test previews to repository discovery, deterministic test-gate
  canonicalization when every criterion is grounded in test-source evidence, validation
  for malformed inline Python and subprocess-output checks, and safe adjudication of
  non-user-answerable critic noise.
- Reduced repeated-file inspection and evidence-free branches, compacted planner history,
  lowered planner variance, reserved a right-sized final-verification budget, and fed
  automatic contract failures directly into the next decision.
- Added one total deadline across provider retries/fallbacks so a failing model request
  cannot multiply the configured timeout across every route.
- Added integer-or-`nonzero` exit contracts and consistent replay semantics for negative
  command assertions.
- Recovered an empty compiler criteria list from the bounded explicit user instruction,
  while still requiring independent criticism and criterion-to-execution evidence; the
  recovery remains fail-closed when repository evidence does not substantiate the goal
  and is recorded in the hashed contract, Receipt, and task UI.

### Reliability

- Made the local in-memory rate limiter preserve the original fixed-window expiry, matching
  Redis behavior.
- Added atomic repository-matrix checkpoints that reject manifest, fixture, runtime, mode,
  or selection drift, plus bounded retry of task publication throttling.
- Expanded no-progress recovery so new failure output is useful evidence while repeated
  output, immediate post-write rereads, duplicate research calls, and unchanged finish
  attempts remain bounded.

### Evaluation

- Added eight protected fixture repositories spanning bug repair, feature work,
  multi-file refactoring, CLI, API, UI, regression preservation, and incomplete
  specifications.
- Added a three-mode evaluator for one-shot, ungated tool loop, and full Loop with
  protected-test hashes, independent double oracle execution, artifact and source
  integrity checks, Receipt replay, Apply/Undo, trajectory taxonomy, and distribution
  reporting.
- Added a disposable Docker-isolated full-Loop matrix runner. Evaluation gates now
  reject any required-isolation downgrade, incomplete model identity, or mixed-model
  fallback, and resume checkpoints bind the requested isolation mode.
- Published the frozen DeepSeek `deepseek-chat` full-Loop matrix: 20/21 deliverable
  attempts solved (95.24%), 3/3 safe specification deferrals, zero false acceptances,
  and one disclosed fail-closed contract-compilation failure.
- Archived the earlier same-model comparison and its exact v0.1 manifest; versioned the
  corrected configuration fixture instead of presenting an eval-spec change as a pure
  product improvement.

### Security

- Pinned patched `brace-expansion` releases across transitive dependency trees.
- Pinned `sharp` 0.35.3 to eliminate inherited libvips vulnerabilities and moved the
  workspace off the broken pnpm 11.13.0 release.

### Documentation

- Defined Loop as the controlled execution layer for loop engineering, mapped the
  agentic coding, developer-feedback, and external-feedback loops, and added ordered,
  testable v0.2 iteration gates.
- Corrected the comparison document to acknowledge the published DeepSeek evaluation
  while retaining its repeated-run and production-isolation limitations.
- Published the first real-provider one-instruction local-project result, including
  contract locking, execution verification, Receipt replay, Apply, and Undo evidence.
- Documented the repository-level protocol, repeated-run distributions, historical
  comparison, current limitations, and the remaining Docker/Kubernetes Gate 4 subgate.

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
