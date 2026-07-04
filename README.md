<div align="center">

# Loop

**Give it a goal. It plans the work, runs it in a sandbox, checks its own output by
re-running it, and produces a receipt you can replay — stopping the moment it hits
a limit you set.**

A complete, production-shaped autonomous agent on a layered Next.js + FastAPI
foundation. Runs on a laptop with one LLM API key and no other infrastructure.

[![CI](https://github.com/chriswu727/loop-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/chriswu727/loop-agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](./LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-162%20offline-brightgreen.svg)](./apps/api/tests)

`Next.js 16` · `FastAPI` · `Python 3.12` · `Postgres or SQLite` · `MIT`

</div>

---

## How it works

```mermaid
flowchart LR
    G([Goal]) --> U[Understand<br/>build a rubric]
    U --> P[Plan<br/>next action]
    P --> A[Act<br/>sandboxed tool]
    A --> O[Observe]
    O --> P
    P -. agent says finish .-> V{Verify<br/>re-run the checks}
    V -. a check fails .-> P
    V == passes ==> R([Receipt<br/>hash-chained, replayable])
```

You give Loop a goal; it runs a **think → act → observe** loop (ReAct):

1. **Understand** — turn the goal into a concrete rubric (success criteria).
2. **Plan** — decide the single next action.
3. **Act** — call a tool: `write_file`, `edit_file`, `read_file`, `run_command`
   (inside a per-task sandboxed workspace), `see_image`, `ask_user`, `spawn`,
   `remember`, or `finish`.
4. **Observe** — feed the result back in, and repeat.
5. **Finish** — an independent **verifier re-runs the machine checks** on a fresh
   copy of the workspace. If the work doesn't hold up, the agent keeps going;
   if it does, Loop writes a tamper-evident **Receipt**.

It stops on the first of: **goal achieved** (verified), **step limit**, **token
budget**, **stuck** (too many failed/blocked actions), or **cancelled** — every
limit clamped to a configured ceiling, so a task can never run away.

## Why Loop — the safe, verifiable alternative

Chat-first personal agents (OpenClaw and its kind) are wildly popular — and, per
independent researchers at **Cisco, Microsoft, Kaspersky and Giskard**, a security
minefield: hundreds of reported vulnerabilities, plaintext credential leaks, and
prompt-injection attacks that have **exfiltrated a real private key from a linked
inbox**. The standing advice is literally "don't run it with your main accounts or
on a machine with sensitive data."

Loop is the agent you *can* leave running unattended. It does the same class of
real work, but is built so those specific attacks can't succeed — and so "done" is
a fact you can replay, not a claim in a chat log.

|                       | Chat-first agents (OpenClaw-style)                | **Loop**                                                  |
|-----------------------|---------------------------------------------------|-----------------------------------------------------------|
| **"Done" means**      | a chat reply — no notion of completion            | a **re-executed, hash-chained Receipt** you can replay    |
| **Shell / tools**     | main session runs on the **host**                 | jailed in an **ephemeral container**, default-deny egress |
| **Skills**            | thousands, **unsigned**, injected into the prompt | **ed25519-signed**, capability-scoped, refused if tampered |
| **Inbound email/DMs** | injection has exfiltrated real private keys       | quarantined as `[DATA]`; sending/acting needs your approval |
| **Secrets**           | plaintext credential leaks reported               | scrubbed from the shell env, masked in tool output, never returned by the API |
| **Reach**             | 20+ chat channels, huge skill marketplace         | web chat, Telegram, browser, email, calendar today         |

Loop concedes raw breadth for now and is closing that gap — but it wins outright on
the two axes a chat-log agent can't retrofit:

- **Verifiable completion.** Every task ships a content-addressed,
  tamper-evident **Receipt** (`receipt.json` + `RECEIPT.md`): the goal, the rubric,
  every machine check the verifier **re-ran on a fresh copy of the workspace**, a
  sha256 of every output file, and the head of a hash-chained step ledger. "Done"
  is a replayable fact — safe to drop into a CI gate (`make verify-receipt`, or
  `scripts/verify_receipt.py` with zero app deps, exits 0/1 and re-hashes the output
  files). Set `AGENT_RECEIPT_SIGNING_KEY` (`make receipt-keygen`) to **ed25519-sign**
  Receipts — then a forger without the key can't recompute a valid one (tamper-*proof*,
  not just evident); verify it offline with `--pubkey`. A run that fell short (a
  limit, a stuck loop, a crash) still ships a Receipt, marked `unverified`, so a
  failure is auditable too.
- **Least authority by construction.** Each task runs under a declared **capability
  envelope** enforced at one choke point: which tools it may use, default-deny
  network egress, and an optional human approval gate for risky commands. Shell
  commands are **jailed in an ephemeral container** (only the workspace mounted, no
  network, can't read the host); the host environment is scrubbed so secrets never
  reach a command. **Skills are ed25519-signed** and refused if tampered. Untrusted
  data (tool output, files, memory) is framed so the agent never obeys instructions
  hidden inside it.

| Differentiator | What it means |
|----------------|---------------|
| **Re-execution Receipt** | the verifier re-runs the agent's checks; a failed check overrides the model's "I'm done" |
| **Tamper-evident ledger** | each step is hash-chained from a genesis; edit any step and `GET /tasks/{id}/ledger` reports it |
| **Signed skills** | a skill bundle's ed25519 signature must verify or it won't load — supply-chain safety |
| **Default-deny egress** | a task can't `curl` / `pip install` / `git clone` unless it declares `allow_egress` |
| **Approval gate** | `require_approval` pauses non-allowlisted commands until you say yes; restart-safe |
| **Injection quarantine** | tool output, files and memory are `[DATA]`, never commands |

## Capabilities

- **Write & run code**, iterating until the checks pass (with self-correction).
- **Edit your documents** — attach an `.xlsx` / `.docx` / `.csv` at publish time and
  the agent edits it in place (openpyxl / python-docx / pandas preinstalled); the
  verifier re-opens the file to prove the edit holds. Outputs are listed and
  **downloadable** from the task view.
- **See images** — with a vision provider (Gemini) configured, the agent can
  `see_image` an uploaded screenshot or photo and act on what it describes.
- **Delegate to sub-agents** — `spawn` hands a self-contained sub-goal to a fresh
  sub-agent that runs its own verified, sandboxed loop and returns a Receipt; a big
  task becomes a *tree* of independently-verified sub-tasks (depth- and
  budget-bounded).
- **Cross-task memory** — a `remember` tool + a `MEMORY.md` the agent reads at the
  start of every task, so it carries knowledge forward.
- **Browse the web** — opt a task into `use_browser` and the agent drives a real
  headless browser via an MCP server it spawns (`@playwright/mcp`): navigate, read,
  click, type, extract. Gated as network egress.
- **Email & calendar** — `use_email` reads the inbox (IMAP, read-only, quarantined)
  and sends (SMTP); `use_calendar` lists and creates events (CalDAV). Anything that
  sends or writes pauses for your approval first.
- **Converse** — group turns into a session (`chat_id`) and follow-ups keep the
  context ("now add tests to it" resolves *it*). A web `/chat` page and a
  channel-agnostic `POST /chat` are the seam any platform plugs into.
- **Chat from Telegram** — set a bot token and command Loop from chat; it runs the
  task, replies, and asks back when it needs input (gated by a chat allowlist).
- **Triggers** — save a task template and fire it from any external event
  (`POST /triggers/{id}/fire`) or on a schedule (interval heartbeat).
- **Human-in-the-loop** — `ask_user` pauses for your input and resumes exactly where
  it left off, surviving a process restart. A finished task can be **retried** with
  the same goal and settings (the original stays as an audit record).
- **Live view** — the task page streams updates over SSE (with a polling fallback):
  step timeline, budget meters, output files, and ledger status.

## Try it in 30 seconds (no API key)

Want to see the verified loop before signing up for anything? A built-in demo model
drives one real task end-to-end — writes `fib.py`, runs it, and the verifier
**re-executes its checks** to produce a Receipt — with **no API key**:

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

Open http://localhost:3000 and try *"Write a Python script that prints the first 12
Fibonacci numbers, then run it to confirm."* — watch it write, run, verify, and
produce a Receipt. For the full Docker stack, `cp .env.example .env`, add a key, and
`make up`.

## Configuration

See [`.env.example`](./.env.example). Key knobs:

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` / `DEEPSEEK_API_KEY` / `GEMINI_API_KEY` / `GLM_API_KEY` | LLM providers (at least one). A retryable failure is retried, then cascades to the next provider. |
| `LLM_DEFAULT_PROVIDER` | which provider to try first. |
| `OLLAMA_BASE_URL` | run on a fully-local model via Ollama (no API key). |
| `API_TOKEN` | optional bearer-token gate on the whole API. |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_ALLOWED_CHAT_IDS` | enable the chat inlet + restrict who can use it. |
| `SMTP_*` / `IMAP_HOST` / `CALDAV_*` | email send/read + calendar (use a Gmail app password). |
| `EXECUTION_MODE` | `inline` (run in the API process) or `worker` (enqueue to Redis). |
| `DATABASE_URL` | `postgresql+asyncpg://…` or `sqlite+aiosqlite:///./loop.db`. |
| `AGENT_APPROVAL_MODE` | `auto` or `manual` (pause non-allowlisted commands). |
| `AGENT_SKILLS_ROOT` / `AGENT_SKILL_TRUST_PUBLIC_KEY` | signed-skills folder + the ed25519 key signatures must verify against. |
| `AGENT_RECEIPT_SIGNING_KEY` | optional ed25519 key to sign Receipts (`make receipt-keygen`); unset = hash-only (tamper-evident). |
| `AGENT_MEMORY_ROOT` | cross-task memory store. |

Per-task safety is set at publish time: `allowed_tools`, `allow_egress`,
`egress_hosts` (restrict egress to named hosts), `require_approval`, `skill`.
Defaults/caps live in `app/core/config.py`.

## Architecture

A layered (ports-and-adapters) FastAPI backend; the agent lives in the service
layer and talks to the model through a provider registry, to the OS through the
tools, and to the DB through repositories — so the loop runs deterministically under
test with a fake model.

```
apps/api/app/
├── core/llm/          # provider registry (Anthropic/DeepSeek/Gemini/GLM/Ollama) + cascade
├── tools/             # workspace sandbox, command policy, egress guard, capability envelope, vision, executor
├── services/
│   ├── agent_react.py # THE ENGINE: understand → plan → act → observe → verify
│   ├── verification.py# re-execution of finish checks on a workspace copy
│   ├── receipt.py     # content-addressed Receipt; ledger.py = hash-chained steps
│   ├── skills.py      # signed, capability-scoped skills
│   ├── chat.py        # shared "message → task → reply" seam (Telegram + HTTP /chat)
│   ├── memory.py      # cross-task memory; trigger.py + scheduler.py = triggers/heartbeat
│   └── task.py        # publish / limits / files / approval-resume
└── api/v1/routes/     # tasks (incl. SSE /events), skills, memory, triggers, chat
```

Design rationale: [`docs/loop.md`](./docs/loop.md). Strategy vs OpenClaw and the
differentiator roadmap: [`docs/STRATEGY.md`](./docs/STRATEGY.md).

## Tests

```bash
cd apps/api && . .venv/bin/activate && pytest    # 162 tests, all offline
```

Drives every stop condition with a scripted fake model; proves the sandbox refuses
path escapes, the command policy blocks dangerous commands, checks gate acceptance,
the ledger detects tampering (and survives a legitimate human answer), skills reject
bad signatures, egress is default-denied, the shell env is scrubbed of secrets, and
the provider cascade falls over correctly.

## Roadmap

**Delivered:** tool-using agent core, re-execution Receipts, tamper-evident ledger,
capability envelope, default-deny egress, approval gate, injection quarantine, signed
skills, document editing, image understanding, cross-task memory, triggers +
scheduler, SSE live view, provider registry, a **local Ollama provider**, an **MCP
client with a headless browser**, **container isolation**, **multi-agent delegation**
(`spawn` → a tree of verified sub-agents), **email + calendar**, **conversational
sessions** with a web chat page, and a **channel-agnostic `/chat` API**.

**Next:** more chat channels (Discord, Slack, WhatsApp), a skill marketplace, and
voice — same agent core and safety model.

## License

[MIT](./LICENSE).
