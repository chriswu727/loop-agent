# 0008 — Empirically-tuned agent behaviour, reliability & runtime safety

- Status: Accepted
- Date: 2026-07-03

## Context

After feature-parity was reached, a round of hardening ran two loops: an
adversarial multi-agent audit of the codebase, and repeated real tasks driven by
`deepseek-reasoner` (the strongest DeepSeek model) with the trace inspected after
each run. Both surfaced concrete, reproducible rough edges — some correctness
bugs, some wasted steps/tokens, some ways to leak or over-reach. This ADR records
the decisions so they aren't silently "tuned back out" later.

## Decision

**Agent-loop behaviour (observed on R1, fixed at the root, A/B-verified):**

- `write_file` and `edit_file` echo a bounded preview (≤20 lines / 1000 chars) of
  the resulting file. The model kept spending a step to `read_file` back what it
  had just written; a prompt rule alone didn't stop it. Echoing the content
  removes the _reason_ to read back. A/B on one task: 6→4 steps, ~30% fewer tokens.
- The planner is told to **finish when the goal is met and the step budget is low**
  rather than chasing minor refinements (e.g. `500` vs `500.0`). Observed a task
  that had the correct answer on disk but fiddled to `max_steps` at score 0; after
  the nudge, same task → `goal_achieved`, score 100.
- A run-time **`notices` channel** injects a prominent `IMPORTANT:` block into the
  planner. First use: when a task opted into `use_browser` but the browser failed
  to start, the agent used to silently run tool-less and could fabricate web
  content. Now it's told, and it reports "could not access the web" instead of
  hallucinating — verified on R1. Reusable for any missing-capability case.

**Reliability:**

- The fallback LLM client **retries the same provider** on a retryable error
  (timeout / 5xx / empty) up to `LLM_MAX_RETRIES` (default 2, linear backoff)
  before cascading. A single-provider setup had no cushion for a transient blip;
  one flaky R1 first-response was failing whole tasks at 0 steps.
- Inline runs are bounded by `AGENT_MAX_CONCURRENT_RUNS` (default 8). Each run
  holds a DB session for its whole duration, so an unbounded burst of publishes
  could exhaust the pool; excess runs queue.

**Runtime safety (defence in depth around the shell surface):**

- Commands run with a **scrubbed environment** (a small allowlist), so an
  allowlisted `env`/`printenv` can't leak the API process's secrets.
- Tool observations pass through a **secret redactor** at the single `_record_step`
  choke point (covers model history, the sealed ledger, and the API), masking
  known secret shapes without touching ordinary text.
- Command output is drained with a **hard byte cap** (kill on overflow) so a chatty
  command can't exhaust host memory; commands run in their own **process group** so
  a timeout kills the whole tree.
- The default-deny **egress denylist** gained the bash `/dev/tcp` socket trick,
  extra fetchers/text-browsers, and network probes. It also now **scans the
  contents of a script a command runs** (`python fetch.py` where `fetch.py`
  imports `urllib`) — found live: a no-egress task wrote a urllib script, ran it
  via an allowlisted `python` command, and actually reached the internet, because
  the guard only inspected the command string. This is still best-effort on the
  inline path; container mode's `--network none` remains the hard enforcement.
- The command **policy regexes had a systematic weakness — they matched only the
  obvious spelling**, so equivalents slipped through. Hardened across the board:
  egress (heredoc/stdin inline code, not just `-c`); the "never run" deny list —
  `rm` recursive-force in any flag form (`--recursive --force`, `-r -f`), named
  fork bombs, `curl | <any interpreter>`, `chmod 777` in any flag order, and
  raw-device writes via `dd of=` / `tee` / `cp` (which also fixed a false positive
  that denied a plain file-to-file `dd`). Each has no-false-positive tests. Same
  caveat: the shell surface is best-effort; container mode is the real jail.

**A critical correctness fix worth remembering:** recording a human answer/approval
used to rewrite the last step's observation _after_ its ledger hash was set, so
`verify_chain` failed for **every** human-in-the-loop task. `respond()` now
re-seals that step's hash. The tamper-evident guarantee only means something if a
legitimate answer keeps the chain valid while tampering still breaks it.

### 2026-07-04 follow-up (auditability, resource bounds, test coverage)

Driven by an R1 run that built correct code but stopped at the step limit (score 0,
no summary, no Receipt) — the awkward case exposed several gaps:

- **A Receipt for every terminal outcome, not only accepted ones.** A limit stop,
  a stuck loop, or a crash now writes a Receipt too, marked
  `verified_by=unverified` — so a failure is auditable (goal, ledger head, file
  manifest of the partial work), not a blank. Built via one best-effort helper
  shared by `_finish` and the crash handler, so a receipt-build error can't mask
  the real outcome.
- **A plain-language summary on non-accepted stops** (`max_steps`/`budget`/`stuck`/
  `cancelled`) instead of a bare score-0 row, telling the user what happened and to
  retry with a higher limit.
- **Cross-task memory is bounded at the file, not just the snapshot.** A single
  `remember` note is capped and each memory file is trimmed tail-most, so a task
  can't bloat the shared store and tax every future task's startup read.
- **Test coverage for load-bearing invariants that were unguarded:** the standalone
  `verify-receipt` script's hash algorithm staying in sync with the library (else
  the offline-verify feature silently breaks), the spawn cost fold-back (a child's
  tokens count against the parent's ceiling), and the loop's resilience to
  unparseable model output.
- **A boot-time warning** when `AGENT_SANDBOX=container/auto` but Docker or the
  image is missing, so an operator sees they're on reduced (inline) isolation.

### 2026-07-04 follow-up (closing the gaps an honest self-comparison found)

A grounded Loop-vs-OpenClaw comparison (`docs/comparison-openclaw.md`) surfaced
concrete weaknesses where the code lagged the pitch. Closed the code-fixable ones:

- **Receipt was only tamper-EVIDENT.** Now: optional ed25519 signing
  (`AGENT_RECEIPT_SIGNING_KEY`), `verify_receipt_full` re-hashes output files
  against the manifest and cross-checks the file hash against the independent DB
  anchor, so editing a fact + recomputing its embedded hash (which the old check
  accepted) is caught. Unsigned Receipts are honestly labeled.
- **"Every terminal task gets a Receipt" wasn't literal** — the skill-refusal path
  now writes one too.
- **A self-written tautological check earned `verified_by=execution`.** The verifier
  now judges whether checks substantiate the goal; if not, the run degrades to
  judgment. The Receipt records coverage (criteria vs checks vs execution-backed).
- **Egress was all-or-nothing.** Tasks can declare `egress_hosts`; the guard blocks
  named destinations off the allowlist (best-effort; container mode unchanged).
- **Email/calendar bypass the container silently.** Now an explicit planner notice
  (they reach the network on the host, sends are approval-gated).
- **A crashed worker stranded its task RUNNING and lost the job.** Staleness-bounded
  reconcile (safe across both modes) + a BLMOVE reliable-dequeue with dead-lettering.

Not code-fixable, stated plainly in the comparison: reach (one chat surface),
ecosystem (one bundled skill), and adoption (zero) — those are maturity, not bugs.

## 2026-07-05 follow-up — a second deepseek-reasoner live-testing round

Running real tasks against `deepseek-reasoner` and reading the traces (and the
server tracebacks, not just the status) surfaced **twelve** defects that unit tests
could not — each a case where the _real model on a real task_ hit something the
mocks never exercised. Two of them (spawn, container) were headline capabilities
that were effectively unusable for the common case yet green in every test that
wasn't a real end-to-end model run:

- **Empty `content` from a reasoning model killed ~1/3 of runs.** R1 intermittently
  returns an empty `content` field with its answer left in `reasoning_content`. The
  adapter read only `content`, so the client saw "empty content", retried (still
  empty — it's prompt/moment-specific, not a transient blip), exhausted retries, and
  the `LLMError` failed an otherwise-complete run. Fix: fall back to
  `reasoning_content` when `content` is empty (`providers.py`). This was found only
  by capturing the actual traceback — the symptom looked like a generic blip.
- **The verifier judged content-only work blind.** `verify_prompts` was fed the
  workspace _tree_ (names + sizes) but never file _contents_, while told to "judge
  only by evidence, never rubber-stamp". Non-executable tasks (a doc, a config, code
  told not to run) therefore always went `stuck`. Fix: a bounded
  `Workspace.contents_digest()` as first-class evidence. Validated: a "write a file,
  don't run it" task went `stuck` → `goal_achieved/100`.
- **Inline demo workspace nested under the app project.** `make demo` put task
  workspaces under `apps/api/`, so an agent's `pytest` inherited the app's
  `pyproject.toml` (`rootdir`, asyncio-auto) and its tests failed no matter what.
  Fix: demo workspace root outside any Python project; documented the inline caveat.
- **Shallow retry budget.** 2 retries / ~1.5s couldn't ride out a multi-second R1
  overload; widened to 4 / ~7.5s (transient blips only — empty content is the case
  above, which retries can't fix).
- **Path-assumption wasted steps.** R1 guessed `cd /home/user` (a container path)
  inline; the prompt now states commands already run in the workspace (no `cd`).
- **Over-asking left done work stuck.** R1 sometimes finished correctly then
  `ask_user`'d a confirmation instead of `finish`, stranding the task in
  `awaiting_input`. Tightened the `ask_user` spec to "pause only when genuinely
  blocked"; validated it still asks on a truly ambiguous goal.
- **Spawn broke the parent's test run (headline capability, common case).** A
  sub-agent's grafted workspace put a second `test_foo.py` in the tree, so the
  parent's pytest hit "import file mismatch" (exit 2) and went `stuck`. Fix: the
  graft excludes cache cruft and drops `subtasks/conftest.py`
  (`collect_ignore_glob=["*"]`) so the archive is compose-only. `stuck` →
  `goal_achieved/100`.
- **Container image lacked pytest (headline capability, common case).** In the real
  isolation path (`--network none`), a task that wrote pytest tests couldn't run
  them and couldn't `pip install` — dead end. Added `pytest` to `sandbox.Dockerfile`
  (`make sandbox-image` to rebuild). `stuck` → `goal_achieved/100/execution`.
- **Fragile JSON extraction wasted steps.** `_extract_json`'s greedy `\{.*\}` regex
  broke on reasoning-model output (dict-braces in prose, multiple objects), yielding
  "invalid action" steps — worsened by the `reasoning_content` fallback feeding it
  raw CoT. Replaced with a brace-balancing scanner that returns the last parseable
  object.
- **The agent had no clock.** Nothing told the model the date, so a "dated report"
  (or any log/changelog) guessed the stale training date or — with shell off — asked
  the user. Inject "Today's date is <YYYY-MM-DD>" into the plan and verify prompts.
- **`max_tokens` starved the reasoning model's answer (root cause of two symptoms).**
  For `deepseek-reasoner` the cap covers the chain-of-thought, so the verify call at
  `max_tokens=500` returned `finish_reason=length` with an EMPTY `content` — the JSON
  verdict never emitted. `_extract_json`→None→verdict defaults to **score 0/met=False,
  rejecting valid work → stuck**; the same starvation produced the intermittent
  "invalid action" plan steps. Captured the raw response to confirm, then raised the
  caps (plan 1200→2500, rubric/verify 500→1500). `max_tokens` is a ceiling not a
  target, so non-reasoning models are unaffected. A doc/README task that was `stuck`
  now completes `goal_achieved/100`.
- **Step-waste sank hard tasks (step efficiency IS a capability lever).** A hard
  LRU+TTL task hit `max_steps` (score 0): R1 kept re-inspecting a just-written file
  with `cat`/`wc` via run_command — circumventing the "never read_file what you just
  wrote" rule — burning ~7 of 22 steps on content it already had, so it never
  converged. Extended that rule to shell re-inspection (advisory, no hard block).
  Validated: the same task went from `max_steps/0` to **3/3 runs `goal_achieved/100`
  with 0 inspections each**, converging even when R1 genuinely iterated (16 steps).
  Pruning avoidable step-waste raises what the agent completes within a budget.
- **Budget checks happened between calls, not around them.** A final model call could
  cross the remaining budget, provider responses with missing usage counted as zero,
  and failed retries disappeared from the task total. Each LLM call now receives a
  spendable sub-budget, reduces its output cap to fit, locally estimates missing usage,
  and conservatively charges failed attempts. Planning cannot consume the verification
  reserve.
- **Successful thrashing looked like progress.** The old `stuck` counter noticed only
  tool errors, blocked actions, and consecutive writes to one path. Equivalent reads or
  searches could all return exit 0 forever. Actions are now fingerprinted against the
  workspace revision, repeated evidence counts as no progress, investigation is branch-
  capped, and workspace changes reset the phase. Older history is compacted into
  artifacts, evidence, and failed branches instead of being discarded.

Lesson reinforced: for an LLM agent, **live-test with the strongest real model and
read the traces/tracebacks** — the highest-value defects (empty-content, verifier
blindness, environment nesting, and whole broken capabilities like spawn-with-tests
and container-with-pytest) are invisible to unit tests and to "status=failed" alone.
Two headline features passed every mock-based test while being unusable for the
common case; only a real end-to-end run surfaced them.

## Consequences

- Fewer wasted steps/tokens per task; cleaner finishes; honest degradation when a
  capability is missing.
- Every terminal outcome — success _or_ failure — is auditable via a Receipt, and
  the Receipt is now tamper-_resistant_ (signed / anchored / file-hashed), not just
  self-consistent.
- More resilient to transient provider errors; bounded resource use under load.
- A tighter shell/secret/egress surface, all covered by offline tests.
- `mypy app` is clean and gated in CI, so these changes can't silently regress
  types. The knobs (`LLM_MAX_RETRIES`, `AGENT_MAX_CONCURRENT_RUNS`,
  `AGENT_REDACT_SECRETS`, `DEEPSEEK_MODEL`) are configurable, defaults chosen for
  the laptop path.
