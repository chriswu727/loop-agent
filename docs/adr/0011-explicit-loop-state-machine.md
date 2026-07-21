# 0011 — Persist an explicit loop state machine

- Status: Accepted
- Date: 2026-07-21

## Context

Task `status` was sufficient for list views but too coarse for a durable autonomous
loop. `running` did not say whether Loop was preparing authority, understanding the
goal, planning, acting, or independently verifying. Pause and recovery behavior was
spread across service branches, and the UI had to infer progress from Steps. Moving or
repairing one branch could therefore create a status combination that another branch
did not understand.

The orchestration service also owned parsing, token allocation, dispatch, acceptance,
and delegation decisions directly. Those decisions were difficult to exhaustively test
without constructing the entire runtime.

## Decision

Persist `loop_state`, `transition_reason`, and `transition_sequence` on every task.
`LoopTransitionPolicy` enumerates allowed events, maps each phase to the compatibility
`status`, and assigns terminal stop reasons. Unlisted transitions fail closed. The
runner's atomic claim and interrupted-task updates persist equivalent reasons and
monotonic sequence changes so recovery does not rely on log interpretation.

Keep `AgentReactService` as the side-effect coordinator, but move pure decisions into
separate transition, decision parsing, context/history, progress, action dispatch,
verification, and delegation policies. Production execution must use those policies;
they are not a parallel test-only model. Expose the persisted state object through the
API contract and task UI.

The state graph explicitly covers fresh completion, input and approval pause/resume,
all bounded stops from every working phase, cancellation and failure from every active
phase, and recovery from interrupted working phases. Automatic claiming excludes human
waiting phases.

## Consequences

Recovery, API clients, and the UI now share one durable lifecycle vocabulary. Transition
tests can enumerate terminal and resumable paths without provider or sandbox setup, and
parsing/budget/dispatch/verification/delegation regressions have small deterministic
tests. The public `status` remains for compatibility, so persistence code must update it
with the explicit phase rather than writing it independently. The coordinator remains
large because it owns runtime resource wiring and side effects; further extraction can
shrink that surface without changing lifecycle semantics.
