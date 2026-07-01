<div align="center">

# Loop

**Publish a goal. An autonomous agent plans it, acts in a sandbox, verifies its
own work, and stops the moment it hits a limit you set — and you can prove,
audit, and trust everything it did.**

A complete, production-shaped agent on a layered Next.js + FastAPI foundation.
Runs on a laptop with one LLM API key and no other infrastructure.

[![CI](https://github.com/chriswu727/loop-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/chriswu727/loop-agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](./LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-109%20offline-brightgreen.svg)](./apps/api/tests)

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

## Why Loop — the safe, verifiable alternative

Chat-first personal agents (OpenClaw and its kind) are wildly popular — and,
per independent researchers at **Cisco, Microsoft, Kaspersky and Giskard**, a
security minefield: hundreds of reported vulnerabilities, plaintext credential
leaks, and prompt-injection attacks that have **exfiltrated a real private key
from a linked inbox**. The standing advice is literally "don't run it with your
main accounts or on a machine with sensitive data."

Loop is the agent you *can* leave running unattended. It does the same class of
real work, but is built so those specific attacks can't succeed — and so "done"
is a fact you can replay, not a claim in a chat log.

|                     | Chat-first agents (OpenClaw-style)            | **Loop**                                             |
|---------------------|-----------------------------------------------|------------------------------------------------------|
| **"Done" means**    | a chat reply — no notion of completion        | a **re-executed, hash-chained Receipt** you can replay |
| **Shell / tools**   | main session runs on the **host**             | jailed in an **ephemeral container**, default-deny egress |
| **Skills**          | thousands, **unsigned**, injected into the prompt | **ed25519-signed**, capability-scoped, refused if tampered |
| **Inbound email/DMs** | injection has exfiltrated real private keys | quarantined as `[DATA]`; sending/acting needs your approval |
| **Secrets**         | plaintext credential leaks reported           | never returned by the API; optional bearer-token gate |
| **Reach**           | 20+ chat channels, huge skill marketplace     | Telegram + browser + email today (more landing)      |

Loop concedes raw breadth for now and is closing that gap — but it wins outright
on the two axes a chat-log agent can't retrofit:

- **Verifiable completion.** Every accepted task ships a content-addressed,
  tamper-evident **Receipt** (`receipt.json` + `RECEIPT.md`): the goal, the
  rubric, every machine check the verifier **re-ran on a fresh copy of the
  workspace**, a sha256 of every output file, and the head of a hash-chained step
  ledger. "Done" is a replayable fact, not a claim — safe to drop into a CI gate.
- **Least authority by construction.** Each task runs under a declared
  **capability envelope** enforced at one choke point: which tools it may use,
  default-deny network egress, and an optional human approval gate for risky
  commands. Shell commands are **jailed in an ephemeral container** (only the
  workspace mounted, no network by default, can't read the host). **Skills are
  signed** (ed25519) and refused if tampered. Untrusted data (tool output, files,
  memory) is framed so the agent never obeys instructions hidden inside it.

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
- **Delegate to sub-agents** — `spawn` hands a self-contained sub-goal to a fresh
  sub-agent that runs its own verified, sandboxed loop and returns a Receipt; a
  big task becomes a *tree* of independently-verified sub-tasks (depth- and
  budget-bounded).
- **Browse the web** — opt a task into `use_browser` and the agent drives a real
  headless browser through an MCP server it spawns (`@playwright/mcp`): navigate,
  read the page, click, type, extract. Same path the email/calendar connectors
  will take. Gated as network egress.
- **Email** — opt a task into `use_email` and the agent can `read_inbox` (IMAP,
  read-only, quarantined) and `send_email` (SMTP) — sending always pauses for
  your approval first.
- **Chat from Telegram** — set a bot token and send tasks from chat; the agent
  runs them, replies with the result, and asks you back when it needs input
  (access-controlled by a chat allowlist).
- **Triggers** — save a task template and fire it from any external event
  (`POST /triggers/{id}/fire`) or on a schedule (interval heartbeat).
- **Human-in-the-loop** — `ask_user` pauses the run for your input and resumes
  exactly where it left off, surviving a process restart.
- **Live view** — the task page streams updates over SSE (with a polling
  fallback): the step timeline, budget meters, output files, and ledger status.

## Try it in 30 seconds (no API key)

Want to see the verified loop before signing up for anything? A built-in demo
model drives one real task end-to-end — writes `fib.py`, runs it, and the
verifier **re-executes its checks** to produce a Receipt — with **no API key**:

```bash
make setup && make demo          # API on :8000, scripted model, DEMO_MODE=1
# then, in another terminal:
NEXT_PUBLIC_API_URL=http://localhost:8000 pnpm --filter web dev
```

Open http://localhost:3000, publish anything, and watch it plan → write → run →
**verify** → Receipt. Then add a real key (below) to point it at your own goals.

## Quickstart (zero infrastructure)

No Docker, no Postgres, no Redis. You need **Python 3.12+**, **Node 20+**, and
**pnpm 10** (`corepack enable`), plus one LLM API key.

```bash
# 1) Backend (FastAPI on SQLite, agent runs in-process)
cd apps/api
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,office]"        # office extras = xlsx/docx/csv editing
export DEEPSEEK_API_KEY=sk-...         # or ANTHROPIC_API_KEY / GEMINI_API_KEY / GLM_API_KEY
export DATABASE_URL="sqlite+aiosqlite:///./loop.db"
export EXECUTION_MODE=inline CACHE_BACKEND=memory
uvicorn app.main:app --port 8000

# 2) Frontend (another terminal, from the repo ROOT)
corepack enable && pnpm install
NEXT_PUBLIC_API_URL=http://localhost:8000 pnpm --filter web dev

# 3) (optional, recommended) build the sandbox image so shell commands run
#    jailed in a container instead of on your host — needs Docker running:
docker build -f apps/api/sandbox.Dockerfile -t loop-sandbox:latest apps/api
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
| `OLLAMA_BASE_URL` | run on a fully-local model via Ollama (no API key). |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_ALLOWED_CHAT_IDS` | enable the chat inlet + restrict who can use it. |
| `SMTP_*` / `IMAP_HOST` | email send/read (use a Gmail app password). |
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
├── core/llm/          # provider registry (Anthropic/DeepSeek/Gemini/GLM/Ollama) + cascade
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
cd apps/api && . .venv/bin/activate && pytest    # ~109 tests, all offline
```

Drives every stop condition with a scripted fake model; proves the sandbox
refuses path escapes, the command policy blocks dangerous commands, checks gate
acceptance, the ledger detects tampering, skills reject bad signatures, egress is
default-denied, and the provider cascade falls over correctly.

## Roadmap

Delivered: tool-using agent core, re-execution Receipts, tamper-evident ledger,
capability envelope, default-deny egress, approval gate, injection quarantine,
signed skills, document editing, cross-task memory, triggers + scheduler, SSE,
provider registry, an **MCP client with a headless browser**, **container
isolation** (shell commands jailed in an ephemeral Docker container), **multi-agent delegation** (`spawn` → a tree of verified sub-agents), a
**local Ollama provider**, **email** (send/read), and a **Telegram chat inlet**.

Next: calendar (over the same MCP/connector pattern) and more chat channels
(WhatsApp, etc.) — same agent core and safety model.

## License

[MIT](./LICENSE).
