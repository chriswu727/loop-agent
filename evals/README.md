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
- `one-instruction-project.json` is the v0.2 flagship fixture. Its evaluator creates a
  clean local Git repository, publishes only the repository path and one instruction,
  requires a criticized and hash-locked generated contract, replays the Receipt, applies
  the verified patch to the source, and proves Undo restores the exact clean repository.
- `repository-suite.json` is the Gate 4 matrix. It contains eight repository fixtures:
  bug repair, feature work, multi-file refactoring, CLI, API, UI, regression preservation,
  and an incomplete specification that must pause safely. `repository-suite-v0.1.json`
  preserves the exact earlier manifest used by the archived three-mode comparison.

## Repository matrix protocol

The repository evaluator supports three modes with the same configured model:

- `one_shot`: one model response returns a complete file bundle, with no repair loop;
- `ungated_loop`: iterative tools without Loop's contract critic, verifier gate,
  progress policy, or completion override; and
- `full_loop`: the shipped contract-first runtime, Receipt replay, change-set Apply,
  external oracle, and Undo path.

Every matrix cell is keyed by repeat, case, and mode. A report is successful only when
all expected cells exist, every case has three repeats, every cell reports one common
model identity, the full Loop solves at least 85% of deliverable attempts, and false
acceptance remains zero. When `--require-isolation` is set, every full-Loop cell must
also report that exact backend. The ambiguous case is excluded from the solve-rate
denominator only when it asks a question without mutating the repository; it is still
counted as a failed safety outcome if it changes files or claims completion.

The harness protects the evidence boundary as follows:

- fixture paths are jailed and symlinks are rejected;
- protected tests are hashed before and after the run;
- expected artifacts must appear in the verified Receipt;
- each external oracle runs on two independent copies, and the candidate source must
  remain unchanged by oracle execution;
- full Loop must pass Receipt replay, Apply the exact verified patch, and Undo back to
  the original clean Git digest;
- checkpoints are atomically replaced with mode `0644` after each cell; resume rejects
  a changed manifest, fixture tree, selected matrix, or evaluator/API runtime; and
- task publication waits through API rate limiting rather than recording HTTP 429 as an
  agent failure.

Manifest oracle commands execute as trusted, repository-owned evaluation code on
temporary copies. Do not point this harness at an untrusted manifest. `inline` mode also
executes model-selected commands without a container boundary and must not be used for
untrusted repositories.

Start an API with a real provider and a disposable local-project root, then run:

```bash
cd apps/api
.venv/bin/python scripts/evaluate_repository_matrix.py \
  --allow-model-spend \
  --base-url http://127.0.0.1:8000 \
  --api-token "$LOOP_API_TOKEN" \
  --project-root "$LOOP_LOCAL_PROJECTS_ROOT" \
  --modes one_shot,ungated_loop,full_loop \
  --repeats 3 \
  --label my-model-repository-matrix \
  --output ../../evals/results/my-model-repository-matrix.json
```

Use `--case <id>` for a canary and `--resume` with the same `--output` after an
interruption. `--allow-model-spend` is mandatory because every selected mode invokes
the configured provider.

For the Gate 4 Docker run, configure a real or local provider in the environment and
use the fail-closed launcher from the repository root:

```bash
make repository-eval-isolated args='--allow-model-spend \
  --repeats 3 \
  --label my-model-container-full-loop \
  --output evals/results/my-model-container-full-loop.json'
```

The launcher builds the sandbox image if necessary, starts a disposable SQLite API,
forces `AGENT_SANDBOX=required` with the Docker backend, runs only `full_loop`, and
removes its temporary projects and state afterward. It refuses to start without Docker,
a configured non-demo provider, explicit spend acknowledgement, and an output path. The
report gate fails if any Receipt does not say `container`; `--resume` also rejects a
checkpoint created with another isolation requirement. Use `--case <id>` for a cheaper
canary before the full run.

For a deployed Kubernetes API, use the direct evaluator command with
`--modes full_loop --require-isolation kubernetes`. The project root supplied to the
evaluator must be the same persistent path visible to that API.

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

To measure the one-instruction local-project path, start the API with a real provider
and `LOOP_LOCAL_PROJECTS_ROOT` pointing at a disposable directory, then run:

```bash
cd apps/api
.venv/bin/python scripts/evaluate_one_instruction_project.py \
  --allow-model-spend \
  --project-root "$LOOP_LOCAL_PROJECTS_ROOT" \
  --label deepseek-chat-one-instruction-v0.1.0 \
  --output ../../evals/results/deepseek-chat-one-instruction-v0.1.0.json
```

The project root must be the same filesystem path seen by the API. The evaluator sends
no criteria, verification commands, artifacts, capability list, or budgets beyond the
fixture's bounded execution limits; those acceptance details must come from Loop.
For inline development, start the API through `make dev` or activate `apps/api/.venv`
first so discovered Python commands use the same environment as the API.

## Recorded result

[`results/deepseek-chat-v0.1.0.json`](./results/deepseek-chat-v0.1.0.json) is one
clean run of all 12 cases with DeepSeek `deepseek-chat`: 12 solved, zero false
acceptances, 30 steps, 42,403 provider-reported tokens, and 65.795 seconds. Every
case passed execution verification, contract coverage, artifact presence, Receipt
integrity, and replay.

[`results/deepseek-chat-one-instruction-v0.1.0.json`](./results/deepseek-chat-one-instruction-v0.1.0.json)
records the flagship local-project fixture with no user-authored criteria, checks,
artifacts, or capabilities. DeepSeek `deepseek-chat` solved the case in 5 steps,
8,610 provider-reported tokens, and 13.459 seconds. The generated contract was locked
before mutation; execution evidence, Receipt integrity, replay, Apply, and Undo all
passed with zero false acceptance.

Both runs used a fresh SQLite database, workspace root, and memory root on macOS. Their
Receipt provenance records `inline` isolation, so they measure the Loop and model
behavior under the explicitly reduced-isolation development path. They do not
measure Docker/Kubernetes isolation, cross-model variance, repeated-run confidence,
or production workload quality. The report records the exact manifest SHA-256 so
the evaluated contract can be matched to the repository.

## Recorded repository results

[`results/deepseek-chat-full-loop-v0.2.12.json`](./results/deepseek-chat-full-loop-v0.2.12.json)
is the frozen release-gate report for its recorded runtime hash. DeepSeek `deepseek-chat`
completed 24/24 cells on the current matrix: 20/21 deliverable attempts solved (95.24%),
3/3 contradictory
specifications safely deferred, and zero false acceptances. Median/p95/max were 4/7/9
steps, 10,386/19,736/25,656 provider-reported tokens, and 16.011/22.589/27.213
seconds. The one failure was an unnecessary UI clarification after the compiler returned
an invalid empty criteria list; it failed closed at step zero and was not accepted.
The runtime now has a focused evidence-gated recovery regression for that failure, but the
published result remains unchanged until a new real-provider run is completed.

[`results/deepseek-chat-repository-matrix-v0.2.0.json`](./results/deepseek-chat-repository-matrix-v0.2.0.json)
is the archived same-model comparison on `repository-suite-v0.1.json`. Across 21
deliverable attempts per mode, one-shot solved 16 with 3 false acceptances, the ungated
loop solved 20 with none, and the earlier full Loop solved 17 with none. It demonstrates
the cost/safety/convergence trade-off and supplied the error analysis for the current
runtime; it is not a direct score comparison with v0.2.12.

The v0.1 configuration goal did not state whether boolean parsing applied only to typed
keys or to every string field. The current manifest explicitly scopes it to keys ending
in `__ENABLED` (normalized to `.enabled`) and preserves unrelated strings. The original
manifest and report remain versioned together. This was an evaluation-specification
correction, so improvement across those two reports must not be attributed solely to
product changes.

Both repository reports used fresh local state on macOS with `inline` isolation and one
provider/model. The final report supplies repeated-run evidence, but it does not measure
cross-model variance, hostile repositories, Docker/Kubernetes isolation, signing, or
production workload quality.
