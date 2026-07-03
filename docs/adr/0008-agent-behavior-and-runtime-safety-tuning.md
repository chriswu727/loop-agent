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
  removes the *reason* to read back. A/B on one task: 6→4 steps, ~30% fewer tokens.
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

**A critical correctness fix worth remembering:** recording a human answer/approval
used to rewrite the last step's observation *after* its ledger hash was set, so
`verify_chain` failed for **every** human-in-the-loop task. `respond()` now
re-seals that step's hash. The tamper-evident guarantee only means something if a
legitimate answer keeps the chain valid while tampering still breaks it.

## Consequences

- Fewer wasted steps/tokens per task; cleaner finishes; honest degradation when a
  capability is missing.
- More resilient to transient provider errors; bounded resource use under load.
- A tighter shell/secret/egress surface, all covered by offline tests.
- `mypy app` is clean and gated in CI, so these changes can't silently regress
  types. The knobs (`LLM_MAX_RETRIES`, `AGENT_MAX_CONCURRENT_RUNS`,
  `AGENT_REDACT_SECRETS`, `DEEPSEEK_MODEL`) are configurable, defaults chosen for
  the laptop path.
