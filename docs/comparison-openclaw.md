# Loop and chat-first personal agents

This is a product-positioning note, not an independent security audit of another
project. It intentionally avoids vulnerability counts, absolute safety claims, and
claims that have not been reproduced in this repository.

## Different optimization targets

Chat-first personal agents optimize for presence: live in the user's existing
channels, stay conversational, and reach a broad skill ecosystem.

Loop optimizes for bounded handoff: accept a goal plus a concrete contract, work
under an authority/budget envelope, independently re-run the evidence, and return a
Receipt that can be checked later.

| Dimension           | Chat-first default                | Loop v0.1                                                      |
| ------------------- | --------------------------------- | -------------------------------------------------------------- |
| Primary interaction | Ongoing conversation              | Goal + acceptance contract                                     |
| Completion          | Model response or workflow end    | Re-executed checks + full criterion coverage                   |
| Artifact            | Transcript and generated files    | Files + hash-chained ledger + replayable Receipt               |
| Authority           | Product/config dependent          | Typed per-task capability envelope at one tool choke point     |
| Network             | Product/config dependent          | Shell/browser grants separated; destinations declared up front |
| Failed work         | Usually visible in the transcript | Terminal unverified Receipt with stop reason and provenance    |
| Reach/ecosystem     | Usually the main advantage        | Narrower and largely first-party today                         |

## What Loop can demonstrate today

- A strict task cannot complete without passing mapped execution evidence.
- The verifier re-runs checks on a fresh copy of the workspace.
- Receipt replay re-runs the captured check definitions and re-hashes outputs.
- Redis worker recovery, authority revocation, and Kubernetes rollback are exercised
  by acceptance jobs in CI.
- The zero-key browser golden path publishes a real task and replays its Receipt.

These are repository-local, reproducible statements. They do not imply that every
possible contract is sufficient, that every model will solve a task, or that prompt
injection is impossible.

## Where Loop remains behind

- It has little adoption history and no large third-party extension ecosystem.
- It has one published 12-case DeepSeek run, but no repeated-run, cross-model, or
  production-isolation benchmark yet.
- Desktop installers are CI-built but not publicly signed, notarized, or auto-updated.
- The safest local path depends on Docker; the zero-key demo uses visibly reduced
  inline isolation.
- Browser sessions are pod-local and are lost when the browser gateway restarts.
- Generic remote MCP transport/auth and a production-isolated Sibyl/Argus gateway are
  not implemented.

## Positioning rule

Loop should win interviews and users by making one narrow promise demonstrably true:

> The agent's claim of completion is not the acceptance decision.

Breadth is useful only when it preserves that property. New integrations are lower
priority than reproducible solve/false-acceptance data, a reliable first run, and
clear residual-risk documentation.
