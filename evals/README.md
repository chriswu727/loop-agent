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
  Its verification commands use `python3` and the standard library so the same
  manifest runs on macOS, Linux, and the sandbox image without host-only aliases.
  Expected artifacts are appended to the task's published acceptance criteria,
  enforced through the API's `required_artifacts` contract, and checked again by
  the scorer; there are no hidden file gates. Cases that ask the agent to write its
  own tests also include visible external behavior assertions, and an empty test
  suite is a deterministic failure even when the runner exits zero.

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
full run. Reports include solve and false-acceptance rates, total and average steps,
tokens and wall time, the manifest hash, per-case model identity, Receipt hashes,
isolation, and replay status.

Real-provider reports are intentionally not fabricated or inferred from offline
tests. Publish one only after running the command and paying the corresponding
provider cost.

## Recorded result

[`results/deepseek-chat-v0.1.0.json`](./results/deepseek-chat-v0.1.0.json) is one
clean run of all 12 cases with DeepSeek `deepseek-chat`: 12 solved, zero false
acceptances, 30 steps, 42,403 provider-reported tokens, and 65.795 seconds. Every
case passed execution verification, contract coverage, artifact presence, Receipt
integrity, and replay.

The run used a fresh SQLite database, workspace root, and memory root on macOS. Its
Receipt provenance records `inline` isolation, so it measures the Loop and model
behavior under the explicitly reduced-isolation development path. It does not
measure Docker/Kubernetes isolation, cross-model variance, repeated-run confidence,
or production workload quality. The report records the exact manifest SHA-256 so
the evaluated contract can be matched to the repository.
