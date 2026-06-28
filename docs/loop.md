# Loop — design notes & decisions

For whoever picks this up next. Why the agent is built the way it is, and the
constraints that aren't obvious from the code.

## The core idea

Loop is an **autonomous ReAct agent with a hard budget and a verifier**. You
give it a goal; it plans one action at a time, uses tools (write files, run
commands) inside a sandbox, observes results, and repeats until a verifier
agrees the goal is met — or a limit stops it. The product values are: it
genuinely *does* things, it *finishes* (not stops early, not loops forever), and
it stays *within the limit*.

The loop (`services/agent_react.py`):

```
understand(goal) -> rubric                      # once, up front (skipped on resume)
repeat:
    plan(goal, rubric, workspace, history) -> {thought, tool, args}
    if tool == finish:
        verify(goal, rubric, summary, workspace) -> {score, met}
        met? -> done (goal_achieved)
        not met, retries left? -> push the gaps back, keep going
        not met, retries spent? -> stop (stuck)
    elif tool == ask_user:
        record the question; pause (awaiting_input); return
        # resumes here when the user answers (see below)
    else:
        observe = execute_tool(tool, args)       # sandboxed
    stop?  step cap | budget | stuck | cancelled
```

Tools: `write_file`, `edit_file` (unique-snippet replace), `read_file`,
`run_command`, plus the loop-handled `ask_user` and `finish`.

## Why these decisions

**Two LLM roles, separated.** The planner proposes actions; an independent
verifier decides "done". A model judging its own "I'm finished" inflates
completion. In the real Fibonacci test run the verifier caught the agent
printing 13 numbers instead of 12 and sent it back — that gap is the whole point.

**The verifier re-executes; it doesn't trust prose (the Receipt).** `finish` can
carry machine-checkable `checks` (a command + expected exit/stdout, file-exists,
file-contains). The verifier re-runs every check on a *fresh copy* of the
workspace (`services/verification.py`) through the same command policy, and a run
with checks is accepted only if its checks actually pass — a failed check
overrides an LLM `met=true`. Every accepted task writes a content-addressed
**Receipt** (`receipt.json` + `RECEIPT.md`, `services/receipt.py`): goal, rubric,
per-check verdict, score, `verified_by` (execution|judgment), run accounting, and
a sha256 of every output file. Goals with no runnable check fall back to
judgment, labelled `verified_by=judgment` so it's never mistaken for proof. This
is differentiator #1 — Loop's "done" is a replayable fact, and it closed Loop's
own real weakness (the old verifier only glanced at the file tree + the summary).

**Capability envelope + executor hooks (the enforcement seam).** Every task runs
under a `CapabilityEnvelope` (`tools/envelope.py`) declaring which executor tools
it may use; it's enforced at the single choke point `ToolExecutor.execute`, so a
tool the envelope doesn't grant returns BLOCKED and never runs. Publish with
`allowed_tools` (the UI's "No shell" toggle sends file-tools only) to run a task
that physically cannot execute commands. The executor also exposes
`before_tool`/`after_tool` hooks — a before-hook may return a result to
short-circuit a call — which is the seam the approval gate, egress firewall, and
signed-skill instrumentation will plug into. Default envelope = full tool set, so
behaviour is unchanged unless a task opts into a narrower one. The planner is also
told its allowed set, so a restricted agent doesn't waste steps on blocked tools.

**Typed approval gate (differentiator #6).** A task published with
`require_approval` pauses before running any command that isn't on the safe
allowlist: the run records an approval-request step (status `blocked`), stores the
pending action on the task, sets `awaiting_input`, and returns — reusing the same
resumable-pause machinery as `ask_user`. `POST /respond` with yes/no decides:
approve and the resumed run executes the stored action as its next step
(recorded "approved by the user"); deny and the action is dropped and the agent
adapts. Every non-allowlisted command gates independently, so the agent stays
autonomous on safe steps and asks only when it wants to escalate. Dangerous
commands are still hard-blocked outright (the gate is for grey-area commands, not
a way to approve `rm -rf /`). This is "autonomy requires a human gate for
privilege escalation" — and unlike OpenClaw's unattended triggers, the gate is
restart-safe because the pause survives a process restart.

**Network egress is default-deny (differentiator #3).** A task cannot reach the
network through the shell unless it declares `allow_egress`. A before-tool guard
(`tools/guards.py`) blocks commands that match network patterns
(`curl`/`wget`/`pip install`/`git clone`/`ssh`/…, see `policy.network_command_reason`)
when the envelope doesn't grant egress, and the planner is told network is off so
it works offline. The UI's "Allow network" toggle opts a task in. Honest caveat:
this is pattern-based v1 — a determined command could still open a socket;
real enforcement (network-namespace deny) arrives with container execution. It
stops the obvious exfiltration path that burned OpenClaw, where outbound channels
plus curl meant an injected "send X out" had a route off the box.

**The step ledger is hash-chained (tamper-evident).** Each step stores
`hash = sha256(prev_hash + canonical(step))`, anchored at a genesis derived from
the task id (`services/ledger.py`). Edit any recorded step — a command, an
observation, a tool arg — and its hash and every hash after it stop matching;
`GET /tasks/{id}/ledger` re-verifies and reports the first broken step. The
Receipt records the chain head, so a Receipt vouches for the whole history that
produced it. This is the "every action auditable and tamper-evident" security
principle made real, and the foundation the signed-skill / approval-gate work builds on.

**No-progress is handled, then forced.** `write_file` always returns "ok", so a
weak model can rewrite one file forever and never run it — a real failure mode
seen in live runs. The loop nudges on the 2nd consecutive write to the same path,
then **hard-blocks the 3rd** (refuses to write, telling it to run the file or do
something else) so a stuck loop becomes forward progress — a previously-flaky
`square(n)` task that looped to `stuck` now converges. Repeated writes also count
toward the stuck limit, so a model that ignores everything still terminates.

**Tools, not raw power.** The agent acts only through `write_file`, `read_file`,
`run_command`, and `finish`. Each is a small, auditable adapter; adding a tool
(web fetch, edit-in-place) is a new entry in `tools/registry.py` and one line in
the prompt's `TOOL_SPECS`. The loop never changes.

**The safety model is two-layered and honest about its limits:**
- *Files* are jailed: `tools/workspace.py` resolves every path inside the task's
  directory and refuses `..`, absolute paths, and symlink escapes. The file
  tools genuinely cannot touch the rest of the disk.
- *Shell* is fenced, not jailed: `tools/policy.py` hard-blocks destructive and
  exfiltration patterns and runs everything from the workspace with a timeout
  and output cap, but a determined command can still read outside the workspace.
  Real isolation needs a container/VM and is a later milestone. This is stated
  plainly so nobody mistakes guardrails for a sandbox jail. Default
  `approval_mode=auto` runs allowlisted + unknown commands and blocks dangerous
  ones; `manual` additionally holds unknown commands for a human.

**Limits clamped in the service.** `TaskService._resolve_limits` applies defaults
then clamps to caps. The "within the limit" guarantee lives in one place,
server-side. Limits are `max_steps` and `token_budget`; "stuck" (N failed/blocked
steps in a row) is the safety net for an agent thrashing without progress.

**Token budget from real usage.** Each provider response reports tokens; the loop
accumulates per planning + verify call and checks before and after each step.

**Inline vs worker execution.** The same `AgentReactService.run` is driven two
ways (`services/runner.py`): `inline` runs it in a FastAPI background task (zero
infra, SQLite-friendly); `worker` enqueues to Redis for a separate process.

**Human-in-the-loop is a resumable loop, not a held-open coroutine.** When the
agent calls `ask_user`, the run records the question, sets `awaiting_input`, and
*returns* — it does not block a worker waiting. `POST /respond` writes the answer
onto the ask_user step, flips the task back to `pending`, and re-triggers the
run. `run()` is resume-aware: it rebuilds working memory from the persisted
steps, skips `understand` if the rubric already exists, and continues from
`steps_used + 1`. This is what lets a pause survive a process restart and, later,
lets the same task be driven from a chat app.

**The publish/respond-then-commit ordering gotcha.** `publish` and `respond`
both commit (and refresh — see below) before the run is scheduled, because the
background/worker agent opens its own session and would otherwise not see the row
or the new status. Keep those commits.

**Always refresh after a server-side onupdate.** `updated_at` is a server
`onupdate`, so after a commit it is *expired*; serializing it then triggers a
lazy load in a sync context and 500s (`MissingGreenlet`). Every mutation that
returns a task (`publish` via create-refresh, `cancel`, `respond`) refreshes
before returning. If you add another, do the same.

**Output files are read straight from the workspace.** `GET /files`,
`/files/{path}` (view) and `/download/{path}` resolve through the same
`Workspace` sandbox, so path-escape protection is shared with the agent's own
file tools — the API can't be tricked into serving `/etc/passwd` either.

**History is windowed.** The planner sees the last `_HISTORY_WINDOW` steps in
full and a count of older ones, so a long run's prompt (and cost) stays bounded.

**Live updates are polling, not SSE.** `app/tasks/[id]/page.tsx` polls every
1.2s. It works in every execution mode (including zero-infra SQLite with no
Redis) and for a single-user agent the latency is invisible.

**SQLite support.** `database_url` is a plain string so `sqlite+aiosqlite://`
works; on that path the engine drops pool args, the cache falls back to
in-memory, and the schema is created on startup (no Alembic). Postgres remains
the production default and uses migrations.

## Data model

- `tasks` — goal, status, rubric (JSON), `max_steps` + `token_budget`, and live
  state: summary, verification_score, steps_used, tokens_used, workspace_path,
  stop_reason, error.
- `steps` — one row per agent step: number, thought, tool, tool_args (JSON),
  observation, status (ok/error/blocked), tokens.

No vendor-specific column types, so the model runs on SQLite and Postgres alike.

## Testing

`tests/test_tools.py` proves the sandbox refuses path escapes and the policy
classifies commands (and that a dangerous command is blocked, not run).
`tests/test_agent_react.py` drives the loop with a scripted fake model so each
stop condition — goal_achieved, max_steps, budget, stuck, and verifier-rejection
— is deterministic and offline. `tests/test_tasks.py` covers the HTTP surface;
its conftest stubs the background trigger so publishing never hits a real model.

The live provider calls and real tool execution are verified by an end-to-end
run (publish a "write and run a script" goal against a real key and watch the
agent create the file, run it, self-correct, and finish).

## Known edges / next steps

- **Cancellation is checked between steps**, so a cancel during a long command
  or LLM call takes effect at the next step boundary.
- **Shell isolation is pattern-based, not a true jail** — the honest gap above.
  Container/VM execution is the next safety milestone.
- **One task per worker process.** Concurrency scales by adding worker replicas.
- **No auth/multi-user.** The starter's auth seam is untouched; scope tasks by
  subject when this becomes multi-tenant.
- **Roadmap:** Telegram/WhatsApp transports (one agent core, many chat inlets),
  Electron packaging, web-research tool, cross-task memory.
