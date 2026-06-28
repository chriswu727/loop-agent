<div align="center">

# Loop

**Publish a task. An agent understands it, drafts it, critiques its own work,
and improves it pass by pass — stopping the moment it hits a limit you set.**

A small, complete, production-shaped app built on a layered Next.js + FastAPI
foundation. LLM-only and side-effect-free, so it's safe to run anywhere.

`Next.js 16` · `FastAPI` · `Python 3.12` · `Postgres or SQLite` · `MIT`

</div>

---

## What it is

Loop is a self-improving agent in a box. You give it a goal; it runs a
**generator–critic loop**:

1. **Understand** — turn the goal into a concrete scoring rubric (success criteria).
2. **Produce** — draft the deliverable.
3. **Critique** — a separate "judge" grades the draft 0–100 against the rubric and
   lists concrete fixes.
4. **Reflect & repeat** — carry the best draft and the critique into the next pass,
   so each pass improves on the last.

It keeps looping until one of these is true, whichever comes first:

| Stop condition       | Meaning                                            |
|----------------------|----------------------------------------------------|
| **Target reached**   | the critic's score meets your target               |
| **Iteration cap**    | it used its allotted passes                        |
| **Budget exhausted** | it spent its token budget                          |
| **Plateau**          | the score stopped improving (diminishing returns)  |
| **Cancelled**        | you pulled the plug                                |

Every limit is a **hard guarantee**, clamped to a configured ceiling, so a single
task can never run away with cost. That's the whole point of "within the limit."

The work the agent does is **producing and refining a text artifact** — a plan, a
draft, an email, a snippet of code. It has no external side effects (no web, no
shell, no file writes), which is what makes it safe to leave running.

## Quickstart

### Option A — zero infrastructure (laptop, SQLite)

No Docker, no Postgres, no Redis. You need Node 20+, Python 3.12+, and at least
one LLM API key.

```bash
# 1) Backend (FastAPI on SQLite, loop runs in-process)
cd apps/api
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
export DEEPSEEK_API_KEY=sk-...        # or GEMINI_API_KEY / GLM_API_KEY
export DATABASE_URL="sqlite+aiosqlite:///./loop.db"
export EXECUTION_MODE=inline CACHE_BACKEND=memory
uvicorn app.main:app --port 8000

# 2) Frontend (in another terminal, from the repo root)
NEXT_PUBLIC_API_URL=http://localhost:8000 pnpm --filter web dev
```

Open http://localhost:3000, publish a task, and watch the loop run.

### Option B — full stack (Docker, mirrors production)

```bash
cp .env.example .env        # add an LLM key
make up                     # web + api + worker + postgres + redis
```

Here the API enqueues each task to Redis and a dedicated **worker** process runs
the loop, so loops scale independently of request traffic.

## Configuration

All via environment (see [`.env.example`](./.env.example)):

| Variable | Purpose |
|----------|---------|
| `DEEPSEEK_API_KEY` / `GEMINI_API_KEY` / `GLM_API_KEY` | LLM providers. At least one required. The loop cascades across configured providers on a retryable failure. |
| `LLM_DEFAULT_PROVIDER` | Which provider to try first (`deepseek` by default). |
| `EXECUTION_MODE` | `inline` (run in the API process) or `worker` (enqueue to Redis). |
| `DATABASE_URL` | `postgresql+asyncpg://…` or `sqlite+aiosqlite:///./loop.db`. |
| `CACHE_BACKEND` | `auto` (in-memory on SQLite, Redis otherwise), `redis`, or `memory`. |

Loop limit defaults and hard caps live in `app/core/config.py`
(`loop_max_iterations_default`, `loop_token_budget_cap`, …).

## How it maps onto the foundation

This is built on a layered (ports-and-adapters) FastAPI backend; dependencies
point inward only. The agent loop lives where business logic belongs — the
service layer — and talks to the model through a provider interface and to the
database through repositories, so it runs deterministically under test with a
fake model.

```
apps/api/app/
├── core/llm/          # provider cascade (DeepSeek → Gemini → GLM) + token accounting
├── domain/            # Task, Iteration, Limits, stop reasons (pure)
├── schemas/           # the API contract (Pydantic DTOs)
├── repositories/      # Task/Iteration data access
├── services/
│   ├── agent_loop.py  # THE ENGINE: understand → produce → critique → stop
│   ├── prompts.py     # the three role prompts
│   ├── task.py        # publish / list / cancel + limit clamping
│   └── runner.py      # inline vs worker execution
├── api/v1/routes/tasks.py   # the HTTP surface
└── workers/worker.py        # @handler("run_task") for worker mode

apps/web/
├── app/page.tsx              # publish form + task list
├── app/tasks/[id]/page.tsx   # the live run view (polls until terminal)
└── components/               # budget meters, score trend, iteration timeline
```

See [`docs/loop.md`](./docs/loop.md) for the design rationale and the decisions
behind it, and [`ARCHITECTURE.md`](./ARCHITECTURE.md) for the underlying skeleton.

## Tests

```bash
cd apps/api && . .venv/bin/activate && pytest
```

The suite drives every stop condition (target, cap, budget, plateau) with a
scripted fake model — no network — so "within the limit" is proven, not assumed.

## Extending it

The action space is deliberately just "refine an artifact." To let the agent
*do* more (web research, code execution, file writes), add a tool interface the
`produce` step can call and gate it behind the same budget. The loop, the limits,
and the UI don't change.

## License

[MIT](./LICENSE).
