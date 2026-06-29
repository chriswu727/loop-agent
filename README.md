<div align="center">

# Loop

**Publish a goal. An autonomous agent plans it, acts in a sandbox, verifies its
own work, and stops the moment it hits a limit you set — and you can prove,
audit, and trust everything it did.**

A complete, production-shaped agent on a layered Next.js + FastAPI foundation.
Runs on a laptop with one LLM API key and no other infrastructure.

`Next.js 16` · `FastAPI` · `Python 3.12` · `Postgres or SQLite` · `MIT`

</div>

---

## What it is

Loop is an autonomous agent that **does real work and proves it**. You give it a
goal; it runs a **think → act → observe** loop (ReAct):

1. **Understand** — turn the goal into a concrete rubric (success criteria).
2. **Plan** — decide the single next action.
3. **Act** — call a tool: `write_file`, `edit_file`, `read_file`, `run_command`
   (inside a per-task sandboxed workspace), `ask_user`, `remember`, or `finish`.
4. **Observe** — feed the result back in, and repeat.
5. **Finish** — an independent **verifier re-runs machine checks** to confirm the
   work, then writes a tamper-evident **Receipt**. If it isn't actually done, the
   agent keeps going.

It stops on the first of: **goal achieved** (verified), **step limit**, **token
budget**, **stuck** (too many failed/blocked actions), or **cancelled**. Every
limit is clamped to a configured ceiling, so a task can never run away.

## Why Loop instead of an OpenClaw-style agent

Loop is designed to be the agent you can leave running unattended. It wins on two
axes a chat-log agent can't easily copy:

- **Verifiable completion.** Every accepted task ships a content-addressed,
  tamper-evident **Receipt** (`receipt.json` + `RECEIPT.md`): the goal, the
  rubric, every machine check the verifier **re-ran on a fresh copy of the
  workspace**, a sha256 of every output file, and the head of a hash-chained step
  ledger. "Done" is a replayable fact, not a claim — safe to drop into a CI gate.
- **Least authority by construction.** Each task runs under a declared
  **capability envelope** enforced at one choke point: which tools it may use,
  default-deny network egress, and an optional human approval gate for risky
  commands. **Skills are signed** (ed25519) and refused if tampered. Untrusted
  data (tool output, files, memory) is framed so the agent never obeys
  instructions hidden inside it.

| Differentiator | What it means |
|----------------|---------------|
| **Re-execution Receipt** | the verifier re-runs the agent's checks; a failed check overrides the model's "I'm done" |
| **Tamper-evident ledger** | each step is hash-chained from a genesis; edit any step and `GET /tasks/{id}/ledger` reports it |
| **Signed skills** | a skill bundle's ed25519 signature must verify or it won't load (`require_approval`-grade supply-chain safety) |
| **Default-deny egress** | a task can't `curl`/`pip install`/`git clone` unless it declares `allow_egress` |
| **Approval gate** | `require_approval` pauses non-allowlisted commands until you say yes; restart-safe |
| **Injection quarantine** | tool output, files and memory are `[DATA]`, never commands |

## Capabilities

- **Write & run code**, iterating until checks pass (with self-correction).
- **Edit your documents** — attach an `.xlsx` / `.docx` / `.csv` when you publish
  and the agent edits it in place (openpyxl / python-docx / pandas preinstalled);
  the verifier re-opens the file to prove the edit holds. Outputs are listed and
  **downloadable** from the task view.
- **Cross-task memory** — a `remember` tool + a `MEMORY.md` store the agent reads
  at the start of every task, so it carries knowledge between tasks.
- **Triggers** — save a task template and fire it from any external event
  (`POST /triggers/{id}/fire`) or on a schedule (interval heartbeat).
- **Human-in-the-loop** — `ask_user` pauses the run for your input and resumes
  exactly where it left off, surviving a process restart.
- **Live view** — the task page streams updates over SSE (with a polling
  fallback): the step timeline, budget meters, output files, and ledger status.

## Quickstart (zero infrastructure)

No Docker, no Postgres, no Redis. Node 20+, Python 3.12+, and one LLM API key.

```bash
# 1) Backend (FastAPI on SQLite, agent runs in-process)
cd apps/api
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]" && pip install -e ".[office]"   # office libs for doc editing
export DEEPSEEK_API_KEY=sk-...        # or ANTHROPIC_API_KEY / GEMINI_API_KEY / GLM_API_KEY
export DATABASE_URL="sqlite+aiosqlite:///./loop.db"
export EXECUTION_MODE=inline CACHE_BACKEND=memory
uvicorn app.main:app --port 8000

# 2) Frontend (another terminal, from the repo root)
NEXT_PUBLIC_API_URL=http://localhost:8000 pnpm --filter web dev
```

Open http://localhost:3000 and try *"Write a Python script that prints the first
12 Fibonacci numbers, then run it to confirm."* — watch it write, run, verify,
and produce a Receipt. For the full Docker stack, `cp .env.example .env`, add a
key, and `make up`.

## Configuration

See [`.env.example`](./.env.example). Key knobs:

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` / `DEEPSEEK_API_KEY` / `GEMINI_API_KEY` / `GLM_API_KEY` | LLM providers (at least one). The loop cascades on a retryable failure. |
| `LLM_DEFAULT_PROVIDER` | which provider to try first. |
| `EXECUTION_MODE` | `inline` (run in the API process) or `worker` (enqueue to Redis). |
| `DATABASE_URL` | `postgresql+asyncpg://…` or `sqlite+aiosqlite:///./loop.db`. |
| `AGENT_APPROVAL_MODE` | `auto` or `manual` (pause non-allowlisted commands). |
| `AGENT_SKILLS_ROOT` / `AGENT_SKILL_TRUST_PUBLIC_KEY` | signed-skills folder + the ed25519 key signatures must verify against. |
| `AGENT_MEMORY_ROOT` | cross-task memory store. |

Per-task safety is set at publish time: `allowed_tools`, `allow_egress`,
`require_approval`, `skill`. Defaults/caps live in `app/core/config.py`.

## Architecture

A layered (ports-and-adapters) FastAPI backend; the agent lives in the service
layer and talks to the model through a provider registry, to the OS through the
tools, and to the DB through repositories — so the loop runs deterministically
under test with a fake model.

```
apps/api/app/
├── core/llm/          # provider registry (Anthropic/DeepSeek/Gemini/GLM) + cascade
├── tools/             # workspace sandbox, command policy, egress guard, capability envelope, executor
├── services/
│   ├── agent_react.py # THE ENGINE: understand → plan → act → observe → verify
│   ├── verification.py# re-execution of finish checks on a workspace copy
│   ├── receipt.py     # content-addressed Receipt; ledger.py = hash-chained steps
│   ├── skills.py      # signed, capability-scoped skills
│   ├── memory.py      # cross-task memory; trigger.py + scheduler.py = triggers/heartbeat
│   └── task.py        # publish / limits / files / approval-resume
└── api/v1/routes/     # tasks (incl. SSE /events), skills, memory, triggers
```

Design rationale: [`docs/loop.md`](./docs/loop.md). Strategy vs OpenClaw and the
differentiator roadmap: [`docs/STRATEGY.md`](./docs/STRATEGY.md).

## Tests

```bash
cd apps/api && . .venv/bin/activate && pytest    # ~89 tests, all offline
```

Drives every stop condition with a scripted fake model; proves the sandbox
refuses path escapes, the command policy blocks dangerous commands, checks gate
acceptance, the ledger detects tampering, skills reject bad signatures, egress is
default-denied, and the provider cascade falls over correctly.

## Roadmap

Delivered: tool-using agent core, re-execution Receipts, tamper-evident ledger,
capability envelope, default-deny egress, approval gate, injection quarantine,
signed skills, document editing, cross-task memory, triggers + scheduler, SSE,
provider registry.

Next (needs infrastructure/keys): container isolation, MCP integrations
(browser / email / calendar), and chat-app inlets — built on the same agent core
and safety model.

## License

[MIT](./LICENSE).
