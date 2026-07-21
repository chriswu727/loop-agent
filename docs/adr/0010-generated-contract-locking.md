# 0010 — Generated contracts are criticized and locked before mutation

- Status: Accepted
- Date: 2026-07-20
- Supersedes: the user-confirmation-only entry path in ADR 0009

## Context

ADR 0009 made strict completion depend on an acceptance contract, but the default
local-project flow required the user to author criteria, commands, and artifacts before
Loop could start. That protected acceptance quality while moving routine orchestration
back to the user. It conflicted with the flagship promise that one instruction should be
enough for well-scoped repository work.

Allowing the executor to invent or weaken its oracle after editing would remove the
protection ADR 0009 established. Repository files and model output are also untrusted:
neither may grant capabilities, introduce hidden network access, or mutate the workspace
during contract construction.

## Decision

For a local-project task without user-authored criteria, Loop clones the clean committed
source and performs bounded read-only discovery. A contract compiler proposes typed
criteria, executable checks, artifacts, assumptions, risk, confidence, and authority
requests. An independently selected verifier model criticizes the proposal before the
executor may act.

The runtime then applies deterministic gates. Every criterion needs execution coverage;
tautologies, policy-denied commands, ungranted execution or shell-network access, and
authority expansion are rejected. Automatic execution is limited to low-risk contracts
at or above the configured confidence threshold. Explicit Advanced checks remain gating
even when criteria are generated.

An accepted `loop.contract-draft/v1` is canonically serialized and content-addressed with
SHA-256. The same locked value, including compiler and critic provenance, drives
planning, completion, Receipt generation, replay, the task UI, and change-set
Apply/Undo. Every resume checks the hash. Critic rejection, invalid model output,
material ambiguity, or unsafe authority becomes `awaiting_input` with no executor step
and no workspace mutation. A clarification triggers fresh compilation; it does not edit
the rejected contract in place.

## Consequences

Routine local changes can begin with a repository and one instruction while retaining a
stable pre-work acceptance source. Contract compilation consumes tokens before execution
and can conservatively ask a question or reject work that a free-form agent might attempt.
Semantic contract quality still depends on the selected models, so deterministic tests
prove control flow and false-acceptance resistance but not broad solve quality. Repeated
real-provider repository evaluations remain a release requirement.
