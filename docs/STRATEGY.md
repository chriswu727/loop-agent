# Product strategy

## Thesis

Loop is the controlled execution layer for loop engineering. The user delegates a
goal, authority, and budget; Loop keeps working, testing, and repairing until it can
return artifacts plus replayable evidence—or an explicit, auditable reason it could
not finish.

Its core differentiator is not another chat surface or another MCP wrapper:

> A model may propose that work is finished, but only the acceptance contract and
> independently re-executed evidence can complete a strict task.

The intended experience is equally important: the user should state the goal once.
Contract construction, repository discovery, routine QA, failure diagnosis, and retry
belong inside the product rather than becoming repeated instructions the user must
write.

## Three nested feedback loops

Andrew Ng describes three loops for building 0-to-1 products with coding agents:
an agentic coding loop, a developer-feedback loop, and an external-feedback loop.
His earlier agentic-workflow work separates reflection, tool use, planning, and
multi-agent collaboration, and emphasizes disciplined evals and error analysis.

This is an engineering framework rather than a claim that iteration alone guarantees
correctness. Loop adopts the framing while adding an explicit control and evidence
boundary.

| Loop               | Owner                   | Typical cadence          | Loop's role                                                                                       |
| ------------------ | ----------------------- | ------------------------ | ------------------------------------------------------------------------------------------------- |
| Agentic coding     | Loop runtime            | Seconds to minutes       | Turn a locked contract into tested artifacts and a Receipt.                                       |
| Developer feedback | User with Loop          | Tens of minutes to hours | Review the product, revise the specification, and turn discovered failures into regression evals. |
| External feedback  | Users and product owner | Hours to weeks           | Feed alpha usage, production behavior, and market learning back into product direction.           |

Loop should own the inner loop and make it reliable. It should make the middle loop
fast and evidence-rich. It should support the outer loop without pretending to replace
human product judgment. Humans retain a context advantage whenever they know something
about users, risk, or intent that the runtime does not.

Primary references:

- [Andrew Ng, “3 Key Loops for Building 0-to-1 Products with AI Agents”](https://www.linkedin.com/posts/andrewyng_loop-engineering-is-a-hot-buzzphrase-after-activity-7477753883768029185-Fg8P)
- [Agentic Design Patterns: Reflection](https://www.deeplearning.ai/the-batch/agentic-design-patterns-part-2-reflection)
- [Agentic Design Patterns: Planning](https://www.deeplearning.ai/the-batch/agentic-design-patterns-part-4-planning)
- [We Iterate on Models. We Can Iterate on Evals, Too](https://www.deeplearning.ai/the-batch/we-iterate-on-models-we-can-iterate-on-evals-too)

## A controlled loop, not repetition

A useful loop needs more than another model call. Every run requires six explicit
elements:

1. **Goal:** a versioned specification and acceptance contract.
2. **State:** the workspace revision, evidence, failures, and attempted branches.
3. **Tools:** bounded ways to observe or change the world.
4. **Verifier:** executable error signals independent of the model's completion claim.
5. **Stop conditions:** success, cancellation, risk, budget, and no-progress limits.
6. **Memory:** compact decisions and evidence that improve the next action without
   carrying an ever-growing transcript.

This is why verification, authority, accounting, recovery, and observability are core
product behavior rather than infrastructure around the “real” agent.

## Product shape

Loop is an application and runtime, not merely a protocol server:

- the web/desktop surfaces collect goals, show progress, and handle material approvals;
- the API persists task state, ownership, contracts, budgets, evidence, and Receipts;
- workers provide durable execution and crash recovery;
- sandboxes and gateways enforce filesystem, process, provider, and network bounds;
- MCP is one adapter family through which specialized capabilities may be exposed.

System design matters because the product promises durable handoff under failure and
least authority—not because it needs speculative internet-scale traffic.

## What v0.1 proved

The v0.1 release established the narrow inner-loop foundation:

- a fresh environment launches the zero-key verified demo with one command;
- strict tasks require mapped passing evidence and replayable Receipts;
- CI exercises the browser journey, dependency audits, desktop packaging, Redis
  recovery, authority revocation, Kubernetes execution, and rollout rollback;
- one recorded DeepSeek `deepseek-chat` run solved all 12 published cases with zero
  false acceptances and a passing replay;
- security boundaries, architecture, implementation, and residual risks are public.

This proves product wiring and one real-provider sample. It does not establish broad
model quality, repeated-run confidence, production adoption, or the complete
three-loop product.

## v0.2 flagship outcome

The next release has one flagship journey:

> Select a real Git repository, enter one instruction, and receive a verified patch
> that can be reviewed, applied, discarded, or undone.

Loop must infer a sufficiently strong contract, discover the repository's checks,
work inside bounded authority, use failures to repair its work, survive interruption,
and present the final evidence without requiring the user to operate the loop.

## Ordered iteration gates

These are release gates, not calendar estimates. Work proceeds in this order and each
gate must be verified before the next one expands the product surface.

### Gate 1 — One-instruction contract compiler

**Implementation status (2026-07-20): gate complete with one real-provider sample.**
The default UI now needs only a repository and instruction. The runtime
performs bounded read-only discovery, compiles and independently criticizes a typed
contract, rejects authority expansion and unsafe verification commands, locks the
accepted draft by content hash before mutation, and exposes the same contract in the
task, Receipt, replay, and Apply/Undo path. Deterministic integration coverage proves
edit → observed failure → repair → independent verification → Apply → Undo, including
the zero-mutation clarification path. The committed DeepSeek `deepseek-chat` fixture
run also completed the whole path from one instruction through Receipt replay, Apply,
and Undo in 5 steps and 8,610 provider-reported tokens. It is one clean sample rather
than a repeated-run confidence claim.

- Replace the default form's manual criteria, command, artifact, capability, step, and
  token configuration with a repository picker, instruction, and Run action. Keep the
  existing controls in an Advanced panel.
- Compile the instruction and deterministic repository discovery into criteria,
  required artifacts, regression checks, capabilities, risk, and budget.
- Run an independent contract critic that rejects tautological, non-verifiable, or
  materially incomplete acceptance criteria.
- Lock the contract before the first mutation. Later executor or verifier output may
  add evidence but may not weaken the locked contract.
- Continue automatically for low-risk, high-confidence work. Ask the user only when
  missing context could materially change the product or authority boundary.

**Done when:** a fresh local-project task can start from one instruction, reach a
strict verified change set, and Apply/Undo without manually authored criteria.

#### First implementation slice

1. Allow a local-project task to be published without user-authored criteria under a
   deterministic, no-network coding capability preset. The model may recommend
   additional authority but can never grant it to itself.
2. After the isolated clone exists, run read-only repository discovery before any
   mutable tool call: manifests, existing quality scripts, test layout, build outputs,
   and the clean baseline.
3. Add a typed `ContractDraft` containing criteria, checks, artifacts, risk,
   assumptions, confidence, and any authority requests. Replace the current generic
   rubric fallback for this path.
4. Ask the verifier/critic to challenge that draft. Persist and hash the accepted
   contract before the executor can mutate the workspace; a material ambiguity or
   authority expansion becomes `awaiting_input`.
5. Feed the same locked contract to planning, completion, Receipt generation, replay,
   the task UI, and evaluation scoring so there is one acceptance source of truth.
6. Add a zero-provider deterministic test and a real-provider fixture proving the
   complete instruction → contract → edit → repair → verification → change-set path.

### Gate 2 — Cancellation, recovery, and concurrency

**Implementation status (2026-07-20): gate complete.** Every run has a database-backed
cancellation watcher. Cancellation tears through the active model/tool coroutine and
its delegated task tree; host process groups, Docker containers, Kubernetes Jobs, and
gateway requests have explicit cleanup tests. Tool execution uses a durable
`loop.operation/v1` write-ahead journal: a crash after the possible side effect but
before the hash-chained Step commit leaves an unknown outcome that recovery refuses to
replay. This deliberately provides at-most-once mutation, not a false exactly-once
claim. SMTP Message-ID and CalDAV UID receive stable operation ids, while already
accepted upstream effects remain non-reversible.

The automated fault matrix covers a failed queue acknowledgement, abandoned Redis
delivery and Redis restart, Postgres container restart, terminal Receipt write failure,
20 duplicate claims, 20 isolated workspaces, 20 source-lock contenders, and 20 runs
through the resource gate. Atomic pending-to-running claims prevent duplicate queue
delivery from executing a run twice; OS-backed project locks serialize Apply/Undo
across processes without stale-lock eviction.

- Propagate cancellation into in-flight provider calls, shell process groups, Docker
  containers, Kubernetes Jobs, gateways, and delegated work.
- Inject crashes around plan persistence, tool completion, verification, queue
  acknowledgement, and terminal Receipt creation.
- Stress duplicate delivery, Redis/Postgres interruption, concurrent project access,
  workspace isolation, source locks, and resource admission.
- Require idempotency for external writes; do not claim exactly-once semantics where
  an upstream system cannot provide them.

**Done when:** automated tests prove prompt cancellation, safe replay/recovery, no
duplicate mutation, and no cross-workspace leakage under at least 20 concurrent tasks.

### Gate 3 — Explicit loop state machine

- Add characterization tests before moving behavior.
- Split the current orchestration service into explicit transition policy, decision
  parsing, context budgeting, progress control, action dispatch, verification, and
  delegation components.
- Persist transition reasons so recovery and the UI use the same state semantics.

**Done when:** every allowed terminal and resumable path is covered by a transition
test, the production loop no longer depends on one monolithic service, and the existing
verified benchmark does not regress.

### Gate 4 — Repository-level evidence and error analysis

- Add realistic fixture repositories covering bug repair, feature work, multi-file
  refactoring, CLI/API/UI changes, regressions, and incomplete specifications.
- Run every case repeatedly and publish distributions rather than a selected best run.
- Compare the same model in one-shot, tool-loop-without-gates, and full Loop modes.
- Evaluate contract quality, trajectory efficiency, tool routing, convergence,
  false acceptance, artifact integrity, and replay in Docker/Kubernetes isolation.
- Turn every recurring failure class into a regression case, and revise evals when
  their ranking disagrees with skilled human judgment.

**Done when:** the primary model reaches at least 85% verified solve rate across three
runs of the repository suite, observed false acceptance remains zero, and the report
includes median/tail steps, tokens, time, questions, and stop reasons.

### Gate 5 — Developer-feedback loop

- Group successive deliveries into a Product Session with versioned specifications,
  feedback deltas, change sets, and Receipts.
- Show evidence and visual/output differences between versions.
- Let a user turn a discovered bug into a persistent regression contract before the
  next run.
- Preserve the distinction between a corrected implementation and a changed product
  decision.

**Done when:** feedback on delivery v1 produces an auditable spec v2 and verified
delivery v2 without losing the v1 contract, evidence, or rollback path.

### Gate 6 — Isolated expert routing

- Move Sibyl and Argus behind an authenticated, production-capable MCP gateway rather
  than host subprocesses.
- Enforce per-server capability, destination, timeout, output, call, and token limits.
- Route Sibyl when a real research uncertainty blocks progress and Argus when UI or
  browser evidence is required; keep both subject to no-progress controls.
- Make health, cancellation, provenance, and failures visible in the Receipt.

**Done when:** a production-isolated research task and UI task automatically use the
appropriate expert, recover from its failure, and still satisfy the same contract and
Receipt semantics.

### Gate 7 — Distribution and external-feedback foundation

- Publish macOS, Windows, and Linux release artifacts with checksums; add signing,
  notarization, and update metadata when credentials are available.
- Make first run validate provider access, sandbox health, repository access, and the
  verified demo before accepting untrusted work.
- Add an opt-in path for a user to attach feedback or a failure report to a Product
  Session without automatically modifying production code.
- Publish a short, reproducible one-instruction-to-Receipt demonstration.

**Done when:** a new user can install Loop, finish the flagship journey, replay its
Receipt, and submit structured feedback without repository-specific assistance.

## v0.2 release gate

v0.2 ships only when all of the following are reproducible:

- the flagship local-repository journey needs one user instruction for routine work;
- generated contracts are locked, independently criticized, and execution-verifiable;
- cancellation, crash recovery, duplicate delivery, and concurrent isolation pass;
- repeated repository-level results and all failures are published honestly;
- a developer-feedback revision retains both specifications and both Receipts;
- release artifacts and a clean-machine first-run path exist;
- README, security boundaries, architecture, evaluation reports, and behavior agree.

## Non-goals

- Claiming that prompt injection or semantic failure is impossible.
- Removing human approval when risk or missing context materially affects the outcome.
- Matching another agent's channel, skill, or sub-agent count before the flagship path
  is proven.
- Letting free-form planning or multi-agent conversation replace deterministic checks.
- Building speculative scaling layers that are not exercised by acceptance tests.
- Treating a green unit suite as proof that the documented user journey works.

## Success metrics

- verified solve and false-acceptance rates by suite, model, isolation, and revision;
- median and tail tokens, steps, questions, tool calls, and wall time;
- contract-critic rejection and human contract-correction rates;
- no-progress stops and useful recovery after a blocked branch;
- Receipt replay after process restart and across a clean environment;
- task recovery after worker loss and duplicate delivery;
- first-run flagship success from a clean installation;
- developer-feedback cycle time and regressions captured from feedback;
- external users who reproduce a Receipt rather than stars alone.
