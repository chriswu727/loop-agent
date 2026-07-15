# 0009 — Contract-first Verified Completion

- Status: Accepted
- Date: 2026-07-15

## Context

Re-running checks supplied by the same agent that performed the work catches many
fabricated completions, but it leaves three trust gaps: the agent can choose an easy
oracle, an existing project failure is indistinguishable from a regression, and a
Receipt can show checks without proving that every user expectation was covered.
Calling all of those results “verified” overstates the product.

## Decision

For local-project work, strict verification starts with a user-confirmed acceptance
contract. The criteria, their source, verification mode, and optional user commands
are persisted before execution and preserved on retry. Loop also discovers native
project lint, typecheck, test, and build commands and runs them before editing to
record a baseline.

A strict task completes only when:

- contract and discovered system checks have been re-run through the normal sandbox;
- every acceptance criterion maps to at least one passing check;
- every contract check passes;
- no system check regresses relative to its pre-change baseline; and
- the independent verifier agrees that the evidence substantiates the result.

Agent-proposed checks may add evidence but are permanently labeled as agent-sourced.
Already-failing system checks remain visible without blocking unrelated work. Explicit
judgment mode remains available for non-executable goals and is labeled Reviewed,
not execution-Verified.

The Receipt records the contract, baseline, evidence mappings, check provenance,
actual executor and verifier model identities, and replay definitions. Applying a
local-project patch requires the same complete execution coverage. The evaluation
suite counts a completed result that fails integrity, coverage, expected-artifact, or
replay gates as a false acceptance.

## Consequences

The publish flow asks for more precision, and baseline checks add startup latency.
In return, “done” no longer depends on an oracle invented after the work and users can
distinguish a newly introduced failure from pre-existing debt. System discovery is
deliberately conservative; ecosystems without a recognized project command need an
explicit contract command or judgment mode until more adapters are added.
