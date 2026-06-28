# Loop — design notes & decisions

For whoever picks this up next. This explains *why* the loop is built the way it
is, and the constraints that aren't obvious from the code.

## The core idea

Loop is a **generator–critic loop with a hard budget**. The product value is
that you can watch an agent improve its own work and trust that it will stop.
Everything else serves those two things: visible improvement, and guaranteed
stopping.

The loop (in `services/agent_loop.py`):

```
understand(goal) -> rubric            # once, up front
repeat:
    produce(goal, rubric, best, last_critique) -> artifact
    critique(goal, rubric, artifact)  -> score, critique
    persist iteration; update best; add to tokens_used
    stop?  target | cap | budget | plateau | cancelled
```

Two separate LLM roles (producer at high temperature, critic at low) is
deliberate — a model grading its own fresh output in the same call inflates
scores. Splitting them gives an adversarial-ish signal.

## Why these decisions

**LLM-only action space.** The agent only produces and refines text. No web,
shell, or file writes. This was a product choice: it makes the app safe to run
unattended and trivial to reason about. The extension seam is the `produce` step
— give it tools and gate them behind the same token budget.

**Limits are clamped in the service, not just validated at the edge.**
`TaskService._resolve_limits` applies defaults then clamps every value to a
configured cap. A user can ask for 9999 iterations; they get the cap. The hard
guarantee lives in one place, server-side. (Target score is additionally bounded
0–100 at the schema, because a score outside that range is meaningless input, not
something to silently clamp.)

**Plateau detection.** Without it, a task that can't reach its target burns every
pass for no gain. We track the *frontier*: if the best score doesn't improve by
`loop_min_gain` for `loop_plateau_patience` consecutive passes, stop. This is why
a run can end at 95/97 after 3 passes instead of grinding to the cap.

**Token budget is enforced from real usage.** Each provider response reports its
token count; the loop accumulates it and checks before and after each pass. The
budget is honored even though we don't pre-count prompt tokens.

**Inline vs worker execution.** The same `AgentLoopService.run` is driven two
ways (`services/runner.py`):
- `inline` — a FastAPI background task runs the loop in-process. Zero infra; the
  whole app runs on SQLite on a laptop.
- `worker` — the API enqueues the task id on Redis and the worker process
  (`workers/worker.py`, `@handler("run_task")`) runs it. Scales independently.

**The publish-then-commit ordering gotcha.** `TaskService.publish` commits the
new row *before* returning, because the run is scheduled as a background task /
enqueue that opens its own session. If we relied on the request's end-of-cycle
commit, the loop's session could query the row before it was committed and find
nothing (`loop.task_missing`). Don't remove that commit.

**Live updates are polling, not SSE.** The plan considered SSE over Redis pub/sub.
We chose polling (`app/tasks/[id]/page.tsx`, 1.2s) instead: it works in every
execution mode including zero-infra SQLite with no Redis, it's simpler, and for a
single-user loop the latency is invisible. If you later need many concurrent
viewers, add an SSE endpoint that subscribes to a `task:{id}` channel the worker
publishes to — the data model already supports it.

**SQLite support.** `database_url` is a plain string (not `PostgresDsn`) so a
`sqlite+aiosqlite://` URL is accepted. On that path the engine drops pool args,
the cache falls back to in-memory, and the schema is created on startup (no
Alembic). Postgres remains the production default and uses migrations.

## Data model

- `tasks` — goal, status, rubric (JSON), the three limits, and live loop state
  (best_score, best_artifact, iterations_used, tokens_used, stop_reason, error).
- `iterations` — one row per pass: number, artifact, score, critique, tokens.

`rubric` is JSON (not a Postgres array) so the model is portable to SQLite. No
vendor-specific column types are used anywhere.

## Testing

`tests/test_agent_loop.py` drives the engine with a `ScriptedLLM` whose critique
scores are dictated by the test, so each stop condition is proven deterministically
and offline. `tests/test_tasks.py` covers the HTTP surface; its conftest stubs the
background trigger so publishing never hits a real model.

The one thing the test suite can't cover offline is the live provider calls — that
is verified by the real-LLM smoke path (publish a task against a real key and watch
it complete within limits).

## Known edges / next steps

- **Cancellation is checked between passes**, so a cancel during a long LLM call
  takes effect when that pass finishes, not instantly.
- **One task per worker process.** Concurrency scales by adding worker replicas.
  There's no global concurrency cap beyond that yet.
- **Provider models are pinned** in `core/llm/providers.py` (`deepseek-chat`,
  `gemini-2.0-flash`, `glm-4-flash`). Bump them there.
- **No auth/multi-user.** The starter's auth seam is untouched; wire it to an IdP
  if this becomes multi-tenant, and scope tasks by subject.
