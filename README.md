<div align="center">

# Loop

**Publish a goal. An autonomous agent plans it, writes files and runs commands
in its own sandboxed workspace, checks its own work, and keeps going until the
goal is done — stopping the moment it hits a limit you set.**

A small, complete, production-shaped agent built on a layered Next.js + FastAPI
foundation. Runs on a laptop with one LLM API key and no other infrastructure.

`Next.js 16` · `FastAPI` · `Python 3.12` · `Postgres or SQLite` · `MIT`

</div>

---

## What it is

Loop is a downloadable-grade autonomous agent. You give it a goal; it runs a
**think → act → observe** loop (ReAct):

1. **Understand** — turn the goal into a concrete rubric (success criteria).
2. **Plan** — decide the single next action.
3. **Act** — call a tool: `write_file`, `edit_file`, `read_file`, `run_command`
   (all inside a per-task sandboxed workspace), or `ask_user` to pause and ask
   you a question when it genuinely needs input.
4. **Observe** — feed the result back in, and repeat.
5. **Finish** — when the agent says it's done, a separate **verifier** checks
   the work against the rubric. If it isn't actually done, the agent keeps going.

When the agent calls `ask_user` the run **pauses** (status *awaiting input*) and
resumes exactly where it left off once you answer. Any files it produces are
**listed and downloadable** from the task view.

**Bring your own files.** Attach a spreadsheet (`.xlsx`), Word doc (`.docx`), or
data file when you publish a task and the agent edits it in place — openpyxl,
python-docx and pandas are preinstalled. "Add a Total column and save it" really
edits your spreadsheet, and the verifier re-opens it to prove the edit holds.

It keeps looping until one of these is true, whichever comes first:

| Stop condition       | Meaning                                            |
|----------------------|----------------------------------------------------|
| **Goal achieved**    | the agent finished and the verifier accepted it    |
| **Step limit**       | it used its allotted steps                          |
| **Budget exhausted** | it spent its token budget                           |
| **Stuck**            | too many failed/blocked actions in a row            |
| **Cancelled**        | you pulled the plug                                |

Every limit is a **hard guarantee**, clamped to a configured ceiling, so a task
can never run away with cost or actions.

## Safety

The agent runs shell commands, which is the riskiest surface in the product, so
it is fenced in:

- **Workspace sandbox** — every file path resolves inside the task's own
  directory; `..`, absolute paths, and symlink escapes are refused.
- **Command policy** — destructive/exfiltration patterns (`rm -rf /`, `sudo`,
  fork bombs, piping the network into a shell, …) are hard-blocked and never
  run. Allowlisted commands run automatically; in `manual` approval mode,
  anything else waits for you.
- **Bounds** — commands run from the workspace, with a timeout and an output
  cap, and the token/step budget bounds the whole run.

This is guardrails, not a jail: it stops the obvious foot-guns. True isolation
(containers) is a later milestone — see [`docs/loop.md`](./docs/loop.md).

## Quickstart (zero infrastructure)

No Docker, no Postgres, no Redis. Node 20+, Python 3.12+, and one LLM API key.

```bash
# 1) Backend (FastAPI on SQLite, agent runs in-process)
cd apps/api
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
export DEEPSEEK_API_KEY=sk-...        # or GEMINI_API_KEY / GLM_API_KEY
export DATABASE_URL="sqlite+aiosqlite:///./loop.db"
export EXECUTION_MODE=inline CACHE_BACKEND=memory
uvicorn app.main:app --port 8000

# 2) Frontend (another terminal, from the repo root)
NEXT_PUBLIC_API_URL=http://localhost:8000 pnpm --filter web dev
```

Open http://localhost:3000 and try: *"Write a Python script that prints the
first 12 Fibonacci numbers, then run it to confirm the output."* Watch the agent
write the file, run it, and verify itself.

For the full Docker stack (web + api + worker + postgres + redis), `cp
.env.example .env`, add a key, and `make up`.

## Configuration

See [`.env.example`](./.env.example). Key knobs:

| Variable | Purpose |
|----------|---------|
| `DEEPSEEK_API_KEY` / `GEMINI_API_KEY` / `GLM_API_KEY` | LLM providers (at least one). The loop cascades across them on a retryable failure. |
| `EXECUTION_MODE` | `inline` (run in the API process) or `worker` (enqueue to Redis). |
| `DATABASE_URL` | `postgresql+asyncpg://…` or `sqlite+aiosqlite:///./loop.db`. |
| `AGENT_WORKSPACES_ROOT` | Where per-task workspaces live. |
| `AGENT_APPROVAL_MODE` | `auto` (block dangerous, run the rest) or `manual` (also hold non-allowlisted commands). |

Step/budget defaults and caps live in `app/core/config.py`.

## Architecture

A layered (ports-and-adapters) FastAPI backend; the agent lives in the service
layer and talks to the model through a provider interface, to the OS through the
tools, and to the database through repositories — so the whole loop runs
deterministically under test with a fake model and fake-free tools.

```
apps/api/app/
├── core/llm/          # provider cascade (DeepSeek → Gemini → GLM) + token accounting
├── tools/             # the agent's hands: workspace sandbox, command policy, shell, registry
├── domain/            # Task, Step, Limits, stop reasons (pure)
├── schemas/           # the API contract (Pydantic DTOs)
├── repositories/      # Task/Step data access
├── services/
│   ├── agent_react.py # THE ENGINE: understand → plan → act → observe → verify
│   ├── prompts.py     # the understand / plan / verify prompts
│   ├── task.py        # publish / list / cancel + limit clamping
│   └── runner.py      # inline vs worker execution
├── api/v1/routes/tasks.py   # the HTTP surface
└── workers/worker.py        # @handler("run_task") for worker mode

apps/web/
├── app/page.tsx              # publish form + task list
├── app/tasks/[id]/page.tsx   # the live run view (polls until terminal)
└── components/               # budget meters, step timeline
```

See [`docs/loop.md`](./docs/loop.md) for design rationale and decisions.

## Tests

```bash
cd apps/api && . .venv/bin/activate && pytest
```

The suite drives every stop condition (goal, step cap, budget, stuck) with a
scripted fake model, proves the sandbox refuses path escapes, and proves the
command policy blocks dangerous commands — all offline.

## Roadmap

1. **(done) Tool-using agent core** — files + shell, sandboxed, budgeted.
2. **Telegram** — talk to the agent and assign tasks from your phone.
3. **Electron desktop** — download, paste an API key, go.
4. **WhatsApp, web research, cross-task memory, container isolation.**

## License

[MIT](./LICENSE).
