# Loop engine design notes

This document captures the invariants that are easy to lose when changing the agent
loop. The product contract is in the README; the deployment boundary is in
`ARCHITECTURE.md` and `SECURITY.md`.

## State machine

The same `AgentReactService.run` is driven by inline and Redis worker execution:

```text
verify any signed skill and resolve the authority envelope
for a local project without a user-authored contract:
  discover repository structure and quality gates without mutation
  compile a typed contract and challenge it with the independent critic
  reject unsafe checks or authority expansion
  pause on material ambiguity, risk, or low confidence
  persist and hash-lock the accepted contract
confirm the persisted contract hash on every resume
discover project checks and record the pre-change baseline
repeat:
  plan exactly one action
  enforce capability, budget, approval, egress, and workspace policy
  execute and persist one hash-chained step
  when finish is proposed:
    copy the final workspace independently for each check
    re-run contract + system + agent checks under the same authority without
      allowing one check's side effects to feed another
    require passing gates and complete criterion coverage in strict mode
    ask the independent verifier for a grounded verdict
    complete only when both layers accept
  stop on goal achieved, step/token limit, stuck, cancellation, or error
write a terminal Receipt
```

`ask_user` and approval do not hold a coroutine or worker open. The task persists its
pending question/action, becomes `awaiting_input`, and returns. `POST /respond` records
the answer and re-triggers a resume-aware run from persisted steps.

## Contract and verification

User criteria become stable ids (`criterion-001`, ...). User verification commands and
required final artifacts become immutable `contract` checks. When a local-project task
starts with only an instruction, Loop first inspects a bounded list of manifests,
scripts, tests, and build outputs without mutation. A compiler proposes observable
criteria, checks, artifacts, assumptions, risk, confidence, and authority requests; an
independent critic challenges that proposal. Deterministic policy additionally rejects
tautologies, uncovered criteria, unsafe or ungranted commands, authority expansion,
non-low risk, and confidence below the automatic-start threshold.

An accepted `loop.contract-draft/v1` is serialized canonically and SHA-256 locked before
the executor receives a mutable tool. Every resume verifies that hash. A rejected draft
persists its issues, asks one material clarification, and returns without consuming an
executor step or changing the clone. User-authored Advanced overrides remain supported
and are locked through the same path. Repository lint/type/test/build scripts become
`system` checks and run once before editing to establish a baseline.

At finish, Loop merges contract, system, and agent-proposed checks, then re-runs each
through the normal tool policy and sandbox on an independent copy of the final
workspace. This prevents a command check from creating state that makes a later check
pass. When the user contract already passes and covers every criterion, agent-proposed
checks remain recorded as supplementary evidence but cannot override that contract.
Without a complete authoritative contract, agent checks remain gating. A strict task
requires:

- every non-baseline failure to pass;
- no new system regression;
- every criterion mapped to passing execution evidence;
- a verifier verdict above the acceptance threshold; and
- checks that substantiate the task, unless authoritative contract checks already do.

Judgment mode is available for work without a meaningful executable oracle and is
displayed as reviewed, not execution-verified.

Every terminal outcome receives `receipt.json` and `RECEIPT.md`. The Receipt binds the
goal, contract, baseline, checks, mappings, model/runtime provenance, accounting,
authority, output hashes, and ledger head. Failure Receipts say `unverified`. Replay
re-runs captured definitions and output hashes; it does not ask the original model.

## Budget and rabbit-hole controls

Limits are clamped in `TaskService._resolve_limits`. Provider-reported usage is
preferred; conservative estimates cover missing usage, and failed retries/fallbacks
are charged. Planning sees only the spendable remainder while verification keeps a
reserve.

The progress guard fingerprints equivalent file, shell, search, MCP, and browser
actions against workspace/evidence revision. It:

- blocks repeated successful actions that add no evidence;
- prevents a second unchanged `finish` from re-running the same failed verifier path;
- caps evidence-free exploration branches;
- nudges repeated writes and hard-blocks the third equivalent write; and
- stops after consecutive no-progress actions.

History keeps recent steps verbatim and deterministically compacts older steps into
artifacts, evidence, failures, and attempted branches. Compaction does not spend a
model call.

## Authority and isolation

`loop.capabilities/v1` is resolved from the task request intersected with any signed
skill grant. `ToolExecutor.execute` is the single enforcement choke point. Planner
visibility is narrower for usability, but runtime enforcement is authoritative.

Command backends:

- `kubernetes`: one short-lived non-root Job per command; production fails closed;
- `container`: ephemeral Docker boundary for laptop/desktop use;
- `inline`: host execution for trusted development/demo tasks, visibly reduced
  isolation.

Filesystem tools reject absolute paths, `..`, and symlink escapes. Network capability
is separate from tool capability. Networked sandboxes receive an audience-bound,
short-lived authority token and can reach only the destination-enforcing proxy.

Approval is restart-safe. Non-allowlisted commands and external writes pause before
execution; hard-blocked dangerous operations do not become approvable.

## Durable execution

Worker mode uses Redis Streams consumer groups with visibility leases, `XAUTOCLAIM`
recovery, bounded retries, and a dead-letter stream. A compare-and-update task claim
prevents duplicate deliveries from executing the same run concurrently. Terminal
runs revoke their authority tokens and close live proxy/browser connections where the
protocol supports it.

The enforcement acceptance harness abandons a claimed job, restarts Redis, and proves
another worker reclaims and finishes it. Kubernetes acceptance additionally exercises
migration, per-command Jobs, Receipt authenticity, NetworkPolicy, a deliberately
broken API rollout, and rollback.

## MCP and provider surfaces

Every external tool is namespaced, schema-discovered, output-capped, and dispatched
through the executor:

- `net.browser` uses the credentialless Browser Gateway in production.
- Email, calendar, and vision use separate credential identities and gateways.
- Development may opt into four Sibyl research and ten Argus QA tools through local
  stdio MCP subprocesses.

Host Sibyl/Argus subprocesses sit outside the task container and are disclosed in
authority metadata. Production refuses them; an isolated generic MCP gateway remains
future work. Repeating an equivalent MCP query is covered by the progress guard.

## Local project transactions

A project task requires a clean source repository under the configured root. It may
start from a user-authored Advanced contract or from one instruction that Loop compiles,
criticizes, and locks before mutation. Loop clones committed content into an isolated
workspace, removes the source remote, and never exposes the absolute source path through
the API.

The resulting patch is bound into the Receipt. Apply re-checks completion, Receipt
integrity, base commit, clean source state, and patch hash. Discard leaves the source
untouched. Undo reverses only the exact applied patch and refuses overlapping changes.

## Persistence gotchas

- Publish/respond commit before scheduling because the run opens its own database
  session.
- Mutations refresh after commit because server-side `updated_at` expires and lazy
  loading from response serialization can otherwise raise `MissingGreenlet`.
- SQLite creates schema at startup for zero-infrastructure development; Postgres uses
  Alembic migrations.
- Output API paths resolve through the same workspace jail as agent file tools.
- Browser calls use SSE for live snapshots with a polling fallback.

## Residual edges

- Cancellation is observed between steps; it does not interrupt an in-flight provider
  request or shell command.
- Protocol operations already accepted by an upstream service cannot be rolled back.
- Browser sessions are pod-local; a gateway restart loses them.
- Inline mode is not a security sandbox.
- A Receipt proves the captured contract and evidence, not semantic correctness beyond
  those checks.
- Real-provider solve quality must be measured with the published evaluation suite;
  offline and deterministic tests do not establish it.
