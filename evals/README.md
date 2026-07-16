# Verified Completion evaluation

This suite measures Loop's central claim instead of model eloquence. A case is
solved only when all of the following remain true after the run:

- the task stopped as `completed / goal_achieved`;
- the Receipt says `verified_by=execution`;
- every acceptance criterion maps to passing execution evidence;
- every expected artifact is in the Receipt manifest;
- the Receipt, ledger anchor, and output hashes are intact; and
- a later Receipt replay passes.

A task that Loop accepted without satisfying every gate is counted separately as
a **false acceptance**. A rejected or budget-limited task is unsolved, but it is
not a false acceptance.

## Suites

- `demo-smoke.json` is the deterministic, zero-cost browser golden path. It proves
  that the product wiring and verification contract work; it is not a model-quality
  benchmark.
- `verified-completion.json` contains 12 small, deterministic coding, data,
  document, CLI, state-machine, and reliability tasks for real-provider runs.

## Zero-cost smoke

Start `make demo`, then run the single smoke case against its API port:

```bash
cd apps/api
.venv/bin/python scripts/evaluate_verified_completion.py \
  --allow-model-spend \
  --api-token "$(cat ../../.demo/token)" \
  --cases ../../evals/demo-smoke.json \
  --label deterministic-demo \
  --output ../../evals/results/demo-smoke.json
```

The flag is an explicit acknowledgement that the command invokes the configured
model surface. `make demo` uses the deterministic mock and incurs no provider cost.
The committed demo report is regenerated during release verification.

## Real-provider benchmark

Run against an already-running API configured with the provider and model being
measured:

```bash
cd apps/api
.venv/bin/python scripts/evaluate_verified_completion.py \
  --allow-model-spend \
  --label deepseek-chat-v0.1.0 \
  --output ../../evals/results/deepseek-chat-v0.1.0.json
```

Use `--case structured-output --case bounded-retry` for a cheaper subset before a
full run. Reports include solve and false-acceptance rates, average steps, tokens,
wall time, per-case model identity, and replay status.

Real-provider reports are intentionally not fabricated or inferred from offline
tests. Publish one only after running the command and paying the corresponding
provider cost.
