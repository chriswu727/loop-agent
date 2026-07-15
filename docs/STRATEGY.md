# Loop — OpenClaw parity + differentiation strategy

> **Implementation snapshot (2026-07-14):** Phases 0–4 in this document are a
> historical build plan, not the current backlog. The repository now ships typed
> capability grants, re-execution Receipts and offline replay, signed skills,
> document uploads, owner/project memory, browser/email/calendar adapters, triggers,
> SSE, Telegram/Slack, GitHub OAuth, durable Redis Streams workers, and required
> Kubernetes Job shell isolation. It now also ships isolated email, calendar,
> vision, and browser gateways; rotatable, revocable audience-bound authority grants;
> and a network-layer egress proxy that requires explicit hosts, rejects private
> resolution, pins DNS, and records durable per-run audit events in horizontally
> shared Redis state. Remaining work that cannot honestly be marked done: a meaningful
> skill marketplace, broader channels, multi-replica browser-session routing, and
> production/adoption evidence.

_Synthesized by a multi-agent research workflow (2026-06): 4 verified OpenClaw research dimensions + repo analysis + a 6-lens differentiation panel. Two research dimensions (capabilities, ecosystem) failed structured-output validation and were covered indirectly via the panel; treat those areas as lower-confidence until re-verified._

## Positioning

Loop is the autonomous agent you can actually trust to run unattended: the only one whose "done" is a re-executed, checkable fact and whose every action runs inside a single declared, enforced authority envelope. OpenClaw optimized for reach and presence — 20+ chat surfaces, a 48h loop, runs its most-used path (your DMs) directly on the host, and extends itself with 5,400+ unsigned plain-English SKILL.md files injected straight into the prompt — which maximizes both its betrayal surface (inbound message -> memory -> outbound channel = a complete injection-to-exfiltration loop) and the gap that it has no independent notion of completion at all. Loop already owns the structural pieces OpenClaw cannot retrofit: a single tool choke point (ToolExecutor.execute in tools/registry.py), a single server-side limit clamp (TaskService._resolve_limits), a complete per-step provenance ledger (the steps table records thought/tool/tool_args/observation/status/tokens), a genuinely jailed file sandbox (workspace.py), and an independent planner!=verifier with a 0-100 acceptance gate. The strategy is to reach full capability parity with OpenClaw — file/office editing, a skills framework, memory, MCP, browser/email/calendar, schedulers — but to win on two axes OpenClaw structurally cannot copy without rebuilding: (1) verifiable completion — every task ships a re-execution-verified, content-addressed, tamper-evident Receipt anyone can replay, making Loop the only agent whose output is safe to drop into a CI gate; and (2) least-authority by construction — signed, capability-scoped skills, default-deny network egress, and universal ephemeral containment, so a malicious skill, an injected message, or a clever shell command can ask for anything but nothing executes outside the envelope the user approved up front. Trust becomes a property of the architecture, not a checkbox you hope you configured.

## Differentiators (ranked)

### 1. Re-execution-verified Receipt (provably-done) _(effort: M (check-based verification) + M (Receipt artifact + hash-chained ledger))_

Turn Loop's seed verifier into the product spine. Today _handle_finish in agent_react.py grades only from workspace.tree() (file names + byte sizes) plus the agent's own prose summary — it never opens a file or re-runs a command, so it largely trusts the claim it checks. Extend the finish tool to require machine-checkable checks (shell command + expected exit code / stdout substring / file sha256); the verifier re-runs each in a fresh executor over a copy of the workspace, and every rubric criterion from _understand must map to at least one passing check. Serialize the whole run into one content-addressed bundle (receipt.json + RECEIPT.md): goal, approved rubric, per-criterion verdict pointing at the proving check, the full step trace, a sha256-per-file manifest, score, model/provider actually used, and token/step accounting — replayable with one command.

**Why it beats OpenClaw / why it's ours:** OpenClaw has no verifier and no completion semantics at all: the agent emits a chat message and the run ends. A chat log can never be re-executed to re-prove a result, so OpenClaw cannot copy this without rebuilding around a verifier and a deterministic trace — both of which Loop already has. This makes Loop the only agent whose output is safe to drop into a CI gate, and it directly closes Loop's own real weakness (the verifier trusting a file-tree glance). Graceful fallback to evidence-based LLM judgment for non-executable goals (essays/designs), clearly labeled as unverified-by-execution.

### 2. Least-authority by construction: one declared, enforced capability envelope per task and per signed skill _(effort: L)_

**Shipped.** The task/skill intersection is enforced in the executor and delegated
to isolated services through short-lived signed grants; the task and Receipt retain
the resolved grant and enforcement audit.

Define every task and every skill to run under a single machine-readable envelope — allowed tools (of the 6), workspace subpaths, egress hosts, command-prefix classes, and step/token sub-budget — enforced as a hard ceiling at the two points Loop already owns: ToolExecutor.execute (tools/registry.py) and TaskService._resolve_limits (services/task.py). Skills ship as signed bundles whose detached signature is verified against a trust root before load; the prose can ask for anything, the runtime grants only what the signature vouched for and the manifest declared. The user sees and approves the envelope up front.

**Why it beats OpenClaw / why it's ours:** OpenClaw's most-used path runs on the host and its extension model is 5,400+ unsigned prose files injected into the system prompt — prompt-injection-as-a-feature with no provenance or capability bound, across a tool surface with no single enforcement point. Loop is the only one of the two with the structure (single choke point + single clamp) to make a malicious or hijacked skill structurally unable to exceed its declared envelope. This is the headline killer feature.

### 3. Default-deny network egress (the exfiltration firewall) _(effort: M)_

**Shipped for shell, browser, email, calendar, and vision traffic.** Sandboxes and
each dedicated gateway have no direct internet or DNS route; the authenticated proxy
enforces audience-bound authority, explicit destination hosts and ports, rejects
non-public resolution, pins the approved IP, and emits a per-run audit trail.

Promote egress to a first-class declared capability enforced at the network-namespace/proxy layer, not by regex. The task container blocks all network by default; the task/skill manifest lists allowed hosts; run_command and any future web/MCP tool can only reach declared destinations. Ship an opt-in profile pre-allowlisting PyPI/npm so normal installs still work.

**Why it beats OpenClaw / why it's ours:** OpenClaw's reach IS its exfiltration surface — it can send out over six-plus chat channels plus curl while inbound messages feed memory, so an injected 'send X to this chat' has a real path off the box. Loop has no outbound channels yet and policy.py already denies piping the network into a shell, so it can adopt default-deny cleanly. Exfiltration in Loop would require a destination the user pre-approved.

### 4. Universal ephemeral containment, default-on (including the primary path) _(effort: L)_

**Shipped in production and the full Compose worker profile.** Production refuses
inline fallback and launches one hardened Kubernetes Job per shell command.

Ship per-task ephemeral containers (Docker default; gVisor/Firecracker as a hardened option) as the default for every task, with only the task workspace bind-mounted and a read-only rootfs. Keep the zero-infra inline mode as an explicit, clearly-labeled reduced-isolation downgrade rather than the silent default.

**Why it beats OpenClaw / why it's ours:** OpenClaw sandboxes only non-main sessions and runs main-session tools on the host — its most-used path is its least contained. Loop inverts that posture and closes its own honest documented gap (docs/loop.md: shell is 'fenced, not jailed,' a determined command can read outside the workspace), making that false by construction. Tension to manage: container cold-start cost vs Loop's runs-on-a-laptop promise, hence the labeled inline fallback.

### 5. Trusted/untrusted content quarantine (data can never become instructions) _(effort: M)_

Loop's loop already separates the trusted goal/rubric from untrusted observations (tool output). Formalize it: wrap every observation and any future inbound/external content (uploaded files, emails, chat messages, memory hits) in a typed 'data, not instructions' envelope the planner is told never to obey, and enforce structurally that a tool call can only originate from the planner's own reasoning channel — never be parsed out of observation text. A capability-escalation request found inside data triggers an approval gate instead of executing.

**Why it beats OpenClaw / why it's ours:** OpenClaw's core injection flaw is mixing untrusted inbound channel messages and auto-indexed memory into the same context as trusted instructions. Loop refuses to act on the exact attack OpenClaw enables by design. Honest framing: prompt injection is unsolved; this raises the bar at the data/instruction boundary, it is not a proof.

### 6. Typed, restart-safe human approval gates (generalized ask_user) _(effort: M)_

Generalize Loop's existing restart-safe pause (ask_user -> awaiting_input -> /respond resumes, survives process restart) into typed capability gates: reaching a new egress host, running a non-allowlisted command, exceeding a sub-budget, deleting files, or sending an email pauses the run with a structured 'about to do X because Y' diff and resumes on a recorded approve/deny. Mostly wiring primitives that already exist: the resumable pause, the policy NEEDS_APPROVAL verdict, and the steps ledger.

**Why it beats OpenClaw / why it's ours:** OpenClaw's autonomous triggers (heartbeat, cron, webhooks, Gmail Pub/Sub) fire with no human in the loop and thin approval handling. Loop makes privilege escalation require an audited human decision, and uniquely the gate is already restart-safe so it survives a crash mid-approval. Mitigate approval fatigue by scoping a grant to the rest of the task and batching related requests.

## Parity matrix (OpenClaw feature -> Loop)

| OpenClaw feature | Loop status | Plan | Effort |
| ---------------- | ----------- | ---- | ------ |

| Agent loop / embedded runtime (intake -> context -> inference -> tools -> stream -> persist) | **have** | Loop's ReAct loop in services/agent_react.py is at parity or ahead: it adds an independent verifier and a rubric OpenClaw lacks. Keep it as the unchanging spine; only widen the tool set and add hook points around ToolExecutor.execute. No architectural change. | S |
| Hard limits / runaway protection | **have** | Loop already clamps max_steps and token_budget server-side in TaskService._resolve_limits and proves every stop condition offline. Extend the same clamp to carry per-task/per-skill sub-budgets (steps, tokens, egress hosts, command classes) so the limit object becomes the capability envelope. | S |
| Built-in first-class tools (shell, file read/write/edit) | **have** | write_file/edit_file/read_file/run_command already exist and dispatch through one executor. Parity for the file/shell core is done; browser/canvas/email/calendar are added in later phases through the same choke point. | S |
| Pluggable LLM providers (provider/model-id, Anthropic/OpenAI/Gemini/local Ollama, etc.) | **partial** | Loop has a 3-provider hardcoded cascade (deepseek/gemini/glm) in core/llm. Generalize to a provider/model-id registry with an openai-completions and anthropic-messages adapter shape, add Anthropic + OpenAI + a local Ollama/vLLM adapter, and make the cascade configuration-driven rather than a hardcoded dict. | M |
| API-key rotation on 429 + model failover chains | **partial** | Loop's FallbackLLMClient already does provider failover on retryable errors. Add per-provider key lists and rotate keys specifically on rate-limit (429/quota) before cascading to the next provider, mirroring OpenClaw's priority order. | S |
| Tool plugin hooks (before/after_tool_call, agent_end) + auto-compaction | **partial** | Loop has history windowing but no hooks or summarization compaction. Add before_tool_call/after_tool_call/finish hook points at the single ToolExecutor.execute choke point (this unblocks skills, receipts, egress enforcement, and approval gates), and add summarization-based compaction beyond the current window. | M |
| Inbound file/attachment ingestion + document editing | **missing** | OpenClaw ingests attachments through channels; Loop has no upload path. Add a file-upload endpoint that lands files in the task workspace through the same Workspace sandbox, and preinstall openpyxl/python-docx/pandas/csv in the runtime image so the agent can edit xlsx/docx/csv. This is the user-chosen Phase 1. | M |
| Skills system (SKILL.md folders loaded into the prompt) | **missing** | Loop has no skills. Build a pluggable skill framework, but as signed, capability-scoped bundles: a machine-readable manifest declares the exact envelope (allowed tools, workspace subpaths, egress hosts, command classes, sub-budget), verified against a trust root before load and enforced at the choke point. Clean-slate advantage over 5,400 unsigned prose files. User-chosen Phase 2. | L |
| Markdown-first memory + SQLite hybrid vector+BM25 search | **missing** | Loop has no cross-task memory (documented gap). Add MEMORY.md (evergreen) + memory/**/*.md auto-indexed into a per-agent SQLite DB with sqlite-vec hybrid vector+BM25 search and temporal decay, matching OpenClaw's design. Pair with skills in Phase 2. | L |
| MCP client (stdio + streamable-HTTP, OAuth/mTLS, toolFilter globs) | **missing** | Add a full MCP client so any MCP server's tools become Loop tools — routed through the same executor and bound by the task/skill capability envelope and egress allowlist. This is also how browser/email/calendar arrive cheaply (existing Gmail/Calendar/Drive MCP connectors). | L |
| MCP server bridge (expose Loop conversations/tasks to external MCP clients) | **missing** | Expose Loop's tasks, steps, files, and Receipts over an MCP server so external clients (Claude, IDEs) can publish tasks and read verified results. Lower priority; after the MCP client and skills land. | M |
| Heartbeat scheduler + cron / webhook / event triggers | **missing** | Loop is one-off only. Add scheduled (cron) tasks and a webhook trigger that publishes a task; reuse the existing publish->trigger path. Every autonomous trigger fires under the same capability envelope and routes privilege escalation through an approval gate (unlike OpenClaw's unattended heartbeat). Phase 3. | M |
| Browser automation, email, calendar capabilities | **missing** | Deliver via MCP connectors + native tools behind the capability envelope: browser through a headless-browser MCP, email/calendar through the existing Gmail/Calendar connectors. Each is egress-allowlisted, its inbound content is quarantined as data-not-instructions, and side-effecting actions (send email, delete event) hit an approval gate. User-chosen Phase 3. | L |
| Multi-agent routing + Docker-sandboxed non-main sessions | **partial** | Loop is single-agent with per-task workspaces but no containers and no routing. Add universal ephemeral containment (Loop's differentiator — default-on, including the primary path, inverting OpenClaw's posture) now; add per-channel agent routing when chat lands in Phase 4. | L |
| Sessions (JSONL persistence) + Secure DM per-sender isolation | **partial** | Loop persists per-task steps and isolates workspaces but has no multi-turn session/thread concept or per-sender isolation. Add a session/thread model and per-sender isolation only when chat-app integration lands (Phase 4, per the standing chat-last rule). | M |
| Local Gateway daemon + typed WebSocket RPC (connect/req/res/event streaming) | **partial** | Loop uses a FastAPI REST control plane with 1.2s polling instead of a long-lived WS gateway — adequate for single-user. Add an SSE/WebSocket event stream for live step streaming (replacing polling) when responsiveness matters; a separate daemon is unnecessary given Loop's server model. | M |
| Self-host install + daemon + companion node apps | **partial** | Loop ships docker-compose and k8s but no one-command installer or companion apps. Add a `loop onboard` installer that brings up the stack and the preinstalled office/runtime image; Electron/menu-bar companions are post-parity polish. | M |
| Subscription OAuth auth (ChatGPT/Codex, Claude CLI, Gemini CLI) | **missing** | Optional flat-rate auth path. Low priority relative to capability parity; revisit after the provider registry lands if users want subscription billing instead of metered keys. | M |

## Roadmap

### Phase 0 — Foundation (do first, unblocks everything) — Add the seams the rest of the plan hangs on, without changing the loop's behavior.

- Add before_tool_call/after_tool_call/finish hook points at the single ToolExecutor.execute choke point (registry.py) — required by skills, receipts, egress enforcement, and approval gates
- Generalize TaskService._resolve_limits into a capability-envelope object (tools, workspace subpaths, egress hosts, command classes, step/token sub-budget) — still defaulting to today's behavior
- Hash-chain the steps table (each row stores H(prev_hash + canonical(step))) to make the existing ledger tamper-evident — pure groundwork for the Receipt
- Upgrade the verifier from a file-tree glance to check-based re-execution: finish accepts machine-checkable checks re-run in a fresh executor over a workspace copy

_Why:_ These are small, low-risk changes to code Loop already owns. They convert Loop's three structural advantages (one choke point, one clamp, one ledger) into extension points, so every later phase is additive rather than a rewrite. Fixing the verifier here also closes Loop's single biggest real weakness immediately.

### Phase 1 — File upload + preinstalled office editing (user-chosen FIRST) — Let users hand Loop real documents and get verified edits back.

- File-upload endpoint that lands files into the task workspace through the same Workspace sandbox (shared path-escape protection with the agent's file tools and download API)
- Preinstall openpyxl, python-docx, pandas, and csv tooling in the runtime image so the agent edits xlsx/docx/csv natively
- Ship Receipt v1 on every task (receipt.json + RECEIPT.md, content-addressed) — document edits are exactly where a checkable 'here is proof I changed cell B7 to X and the file still opens' matters
- Bring up universal ephemeral containment as the default execution mode here (office libs + uploaded files are the natural first container image); keep inline as a labeled reduced-isolation fallback

_Why:_ Honors the user's chosen order and pairs each capability with the differentiator it most needs: uploaded documents are untrusted input (containment) and editing them demands provable, re-openable output (Receipt). Delivers visible end-user value (edit my spreadsheet) on day one of the roadmap.

### Phase 2 — Pluggable skills framework, the Loop way (user-chosen SECOND) — Reach OpenClaw's extensibility while inverting its trust model, and add the memory/MCP that skills lean on.

- Signed, capability-scoped skill bundles: manifest declares the envelope, detached signature verified against a trust root before load, enforced at the choke point (this is the killer differentiator)
- Skill loader with hot-reload in dev and a skills-snapshot injected into the system prompt, matching OpenClaw's ergonomics
- Cross-task memory: MEMORY.md + memory/**/*.md auto-indexed into per-agent SQLite with sqlite-vec hybrid vector+BM25 search and temporal decay
- MCP client (stdio + HTTP, toolFilter globs) so skills can wrap external tools — every MCP tool bound by the task/skill envelope
- Default-deny egress firewall + typed restart-safe approval gates go live here, because untrusted third-party skills are the moment capability enforcement must be real

_Why:_ Skills, memory, and MCP are interdependent (skills reference memory and wrap MCP tools), so they ship together. This is the phase where Loop's security thesis stops being a claim and becomes load-bearing: the moment you load a stranger's skill, the envelope, signing, egress firewall, and approval gates must all be in force.

### Phase 3 — Big capabilities: browser, email, calendar (user-chosen THIRD) — Match OpenClaw's flagship reach for getting real-world work done.

- Browser automation via a headless-browser MCP, behind the capability envelope + egress allowlist
- Email and calendar via the existing Gmail/Calendar/Drive MCP connectors; all inbound content (emails, events) quarantined as data-not-instructions
- Side-effecting actions (send email, delete/modify calendar event, browser POST) route through the typed approval gate and land in the Receipt
- Heartbeat/cron scheduled tasks + webhook triggers so Loop can act on a schedule — every autonomous trigger still runs under an envelope and escalates to a human gate, unlike OpenClaw's unattended heartbeat
- Expand the provider registry (Anthropic/OpenAI/local Ollama) + add 429 key rotation; add SSE event streaming to replace polling

_Why:_ Delivers the capabilities the user explicitly wants, but each one is also the highest-risk injection/exfiltration surface — so it ships only after quarantine, egress firewall, approval gates, and containment exist. This is where Loop's safety architecture earns its keep: browser/email/calendar is precisely the inbound-to-outbound loop that burned OpenClaw.

### Phase 4 — Chat-app integration (LAST, per standing rule) — Make Loop reachable on messaging surfaces without inheriting OpenClaw's betrayal surface.

- Channel inlets (Telegram/WhatsApp/etc.) feeding the existing publish->resume path — the loop core does not change
- Session/thread model + Secure DM per-sender isolation
- Per-channel/per-peer agent routing with each agent under its own envelope and container
- Companion apps (Electron/menu-bar) and a `loop onboard` one-command installer
- MCP server bridge so external clients can publish tasks and read verified Receipts

_Why:_ Honors the user's standing rule that chat-app integration comes last. By this point every inbound message arrives quarantined as data, every outbound action is egress-allowlisted and gated, and every agent is contained — so Loop adds reach as its final step, on top of a trust model that OpenClaw added reach before having.

## Security principles (distilled from OpenClaw's failures)

- The most-used path must be the most contained, not the least. OpenClaw runs its main session (your DMs) on the host and sandboxes only group/channel sessions. Loop containerizes every task by default, primary path included.
- Untrusted data must never become instructions. OpenClaw mixes inbound channel messages and auto-indexed memory into the same context as trusted commands. Loop wraps every observation, upload, email, and memory hit in a typed data-not-instructions envelope the planner is told never to obey, and a tool call can only originate from the planner's own channel.
- Extensions must carry provenance and a declared, enforced capability envelope. OpenClaw's 5,400+ SKILL.md files are unsigned prose injected into the prompt. Loop requires a signed bundle whose manifest declares an exact envelope, verified before load.
- Egress is a declared capability, default-deny. OpenClaw treats sending over many channels as core function, leaving dozens of outbound paths open. Loop blocks all network by default and allows only pre-approved, manifest-declared hosts, enforced at the network layer not by regex.
- Autonomy requires a human gate for privilege escalation. OpenClaw's heartbeat/cron/webhook/Pub/Sub triggers fire unattended. In Loop, reaching a new host, running a non-allowlisted command, exceeding a sub-budget, or any side-effecting action pauses for an audited, restart-safe approval.
- Every action must be auditable and tamper-evident. OpenClaw emits ephemeral events and JSONL transcripts, not a reviewable, verifiable security record. Loop hash-chains its per-step ledger and ships a signed, content-addressed Receipt of every command, file, host, capability grant, and approval.
- Enforce at the runtime choke point, not in configuration or prose. A manifest's words and a skill's instructions cannot be trusted to describe what they will attempt; enforcement lives at ToolExecutor.execute and the limit clamp, which the manifest can only narrow, never widen.
- Capability is a hard ceiling, declared up front and shown to the user. Trust must be a structural property the user approved before the run, not a setting they hope they configured correctly.

## Open questions for the user

- Default execution mode: ship ephemeral containers as the default (closing the honest shell gap but adding cold-start latency and macOS Docker cost) versus keeping the zero-infra inline mode as default and labeling containers opt-in? Your runs-on-a-laptop value pulls against default-on containment — which side wins?
- Provider registry priority: the current cascade is deepseek/gemini/glm (cost-optimized). When generalizing to provider/model-id, which do you want first — Anthropic, OpenAI, local Ollama/vLLM? And do you want subscription-OAuth (flat-rate) auth, or is metered-key only fine?
- Skill signing trust root: at launch, BYO trust root only (you sign your own skills), or do you want a hosted Loop skill registry with a community trust root? The latter is a cold-start and key-distribution problem.
- Office editing delivery: preinstall openpyxl/python-docx/pandas in one shared runtime image, or a heavier per-task container image? This affects image size and container cold-start, which ties back to the default-execution question.
- Multi-tenancy timing: there is no auth today and memory/Receipts are single-user. Should we build the per-subject scoping seam now (so memory and Receipts are tenant-ready), or defer until chat-app integration forces it in Phase 4?
- Browser/email/calendar build vs buy: prefer wrapping the existing Gmail/Calendar/Drive MCP connectors and a headless-browser MCP (fast, less code, inherits their auth), or native integrations (more control, more surface)? Wrapping is the faster path to parity.
- Receipt scope for non-executable goals: for essays, designs, and research where there is no command to re-run, is it acceptable to fall back to evidence-based LLM judgment clearly labeled 'verified-by-judgment, not by execution', or do you want those tasks to require at least one mechanical check (e.g., file-exists, word-count, link-resolves) before finish is accepted?

---

## Decisions locked (2026-06, with the user)

1. **Default execution mode = ephemeral container by default**, with `inline` as an explicit, clearly-labeled reduced-isolation downgrade. Closes the "shell is fenced, not jailed" gap; the laptop/zero-infra path stays available but is no longer the silent default.
2. **First new provider = Anthropic, with subscription-OAuth (flat-rate) auth** supported (not just metered keys). Keep deepseek/gemini/glm as the existing cascade/fallback. Generalize `core/llm` to a provider/model-id registry.
3. **Receipt for non-executable goals = judgment fallback allowed, but clearly labeled** "verified-by-judgment, not by execution". Executable goals must carry machine-checkable checks the verifier re-runs.

## Build order (current)

Phase 0 foundation first — starting with the verifier → checks + Receipt upgrade (differentiator #1, and it fixes Loop's own real weakness).
