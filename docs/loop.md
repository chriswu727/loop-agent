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
understand(goal) -> rubric                      # once, up front
repeat:
    plan(goal, rubric, workspace, history) -> {thought, tool, args}
    if tool == finish:
        verify(goal, rubric, summary, workspace) -> {score, met}
        met? -> done (goal_achieved)
        not met, retries left? -> push the gaps back, keep going
        not met, retries spent? -> stop (stuck)
    else:
        observe = execute_tool(tool, args)       # sandboxed
    stop?  step cap | budget | stuck | cancelled
```

## Why these decisions

**Two LLM roles, separated.** The planner proposes actions; an independent
verifier decides "done". A model judging its own "I'm finished" inflates
completion. In the real Fibonacci test run the verifier caught the agent
printing 13 numbers instead of 12 and sent it back — that gap is the whole point.

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

**The publish-then-commit ordering gotcha.** `TaskService.publish` commits the
new row before the run is scheduled, because the background/worker agent opens
its own session and would otherwise not see the row (`agent.task_missing`). Keep
that commit.

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
