# Security

Loop treats the model, skills, uploaded files, tool output, memory, and external
messages as untrusted. Security decisions are enforced by code and infrastructure,
not prompt instructions.

## Identity and tenant isolation

- Production sets `AUTH_REQUIRED=true` and `WEB_AUTH_REQUIRED=true`.
- GitHub login uses authorization code, random `state`, and PKCE. The GitHub access
  token is used once to call `/user` and is not stored.
- The web tier issues an eight-hour HTTP-only, `SameSite=Lax` Loop JWT. The API
  verifies its HS256 signature, expiry, issuer, and audience.
- Tasks, triggers, memory, files, Receipts, and idempotency keys are scoped to the
  JWT subject. A cross-owner lookup returns 404.
- `SECRET_KEY` and `LOOP_SESSION_SECRET` must be the same random value and at least
  32 bytes. Rotate them together; rotation invalidates existing web sessions.

## Runtime authority

- Each task declares `loop.capabilities/v1`; omitted legacy fields are converted at
  the boundary. A signed skill may only narrow the task grant, including its explicit
  destination-host policy.
- `ToolExecutor.execute` is the enforcement choke point. Unknown tools and missing
  capabilities default-deny.
- `net.browser` does not grant `net.shell`; email, calendar, memory, vision, and
  delegation are independent grants.
- Side-effecting email/calendar actions and policy-classified commands pause through
  the persisted approval flow when approval is required.
- The worker signs an audience-bound `loop.authority-token/v1` for each run. Tokens
  carry the task/owner/project/run identity, exact capabilities, explicit destination
  hosts, and a short expiry. Gateway and proxy processes have only the
  public verifier, so a compromised enforcement service cannot mint wider grants.
- Verification uses the token's derived Ed25519 `kid` against a configured keyring.
  A terminal run sends a separately audience-bound control token to every enforcement
  services; they persist the run revocation, reject its remaining tokens, close its
  browser session, and tear down established proxy connections.

## Sandbox and network

- Production uses `AGENT_SANDBOX=required` with the Kubernetes backend. If the
  backend or image is unavailable, the task fails; there is no host fallback.
- Each shell command runs in a fresh Job: non-root UID, read-only root filesystem,
  no service-account token, all Linux capabilities dropped, no privilege escalation,
  resource/time limits, and only the shared task volume plus `/tmp` mounted.
- Only the current task directory is mounted through a PVC `subPath`; other tenants'
  workspaces and memory are not visible. Production requires the sandbox image to be
  pinned by digest, and Receipts record that digest.
- Shell egress is denied unless `net.shell` and at least one explicit host were
  granted. Networked Jobs can connect only to the egress proxy. The proxy verifies
  the run token, exact host/subdomain policy and allowed port, rejects loopback,
  private, link-local and otherwise non-global addresses, resolves once, and connects
  to that pinned IP to prevent DNS rebinding. Every allow/deny is available to the
  worker audit endpoint and is embedded in the task/Receipt.
- The worker resolves the internal proxy service before creating a sandbox and passes
  only its IP. Sandbox DNS is disabled locally and not allowed by Kubernetes
  NetworkPolicy, closing DNS-query exfiltration while target DNS remains inside the
  audited proxy.
- Local `preferred` mode may fall back to the host and labels the Receipt accordingly.
  Use `required` when containment is mandatory.
- Email, calendar, and vision credentials exist only in their respective dedicated
  gateways. Chromium runs in a fourth gateway with no provider credentials. All four
  have DNS disabled and no direct internet route; they can reach only the authenticated
  proxy and the internal enforcement-state Redis service. The worker uses separate
  audience-bound grants, and production fails closed if any required gateway,
  shared-state backend, or upstream-host policy is missing.

### Exact guarantee boundaries

- Shell containers/Jobs have no direct external route: destination enforcement is a
  network-layer property of the sandbox namespace plus proxy.
- Browser, SMTP, IMAP, CalDAV, and vision traffic is routed through the authenticated
  proxy. Each gateway identity may connect only to the proxy and internal Redis ports
  and has DNS disabled. Gateway-local relays inject short-lived proxy authority
  without exposing it to Chromium or upstream libraries. The gateway and proxy both
  verify the exact identity, capabilities, and host set before the proxy resolves and
  pins a public IP.
- Proxy audit is a bounded, horizontally shared Redis Stream. Run revocations are a
  shared sorted set plus Pub/Sub notification, so a revocation observed by one replica
  tears down live connections on the others. These operational events are embedded in
  the task and Receipt; use an external append-only sink when compliance retention
  must exceed the bounded stream.
- Chromium sessions are still pod-local. The base Browser Gateway remains a single
  `Recreate` replica; scaling it requires sticky run routing or an external browser
  session backend.
- Revocation prevents new calls and closes browser/proxy connections, but cannot roll
  back a completed side effect or guarantee interruption of an SMTP/CalDAV operation
  already accepted by an upstream service. API cancellation polls durable task state,
  cancels the in-flight coroutine, tears down sandbox processes/connections, and then
  publishes signed revocation. Host-only development SMTP/CalDAV adapters use blocking
  libraries; cancellation stops awaiting them but cannot forcibly stop their worker
  thread, which is another reason production routes these capabilities through gateways.
- Before any tool side effect, Loop commits a durable operation id and action. The Step
  and journal deletion commit atomically after execution. Recovery never blindly replays
  a journal left in flight; it fails closed because the upstream outcome is unknowable.
  Email Message-ID and calendar UID carry that operation id, but this is not an
  exactly-once guarantee.

## Receipts and provenance

- `loop.receipt/v1` records criteria-to-check mappings, resolved authority, model and
  verifier identities, revision, runtime, sandbox image/digest, output hashes, and the
  step-ledger head.
- Hash verification provides integrity. Production refuses to start without a valid
  Ed25519 signing key; publish its public key so independent verifiers can establish
  authenticity. Development may emit unsigned Receipts, which are never labeled
  authentic.
- API and CLI replay verify Receipt integrity and output hashes before executing any
  recorded check. Command replay refuses the host unless explicitly allowed.

## Secrets and supply chain

- Never commit secrets. `.env` is ignored; the example Kubernetes Secret is not
  part of Kustomize resources, so a deployment cannot silently inherit placeholders.
- API, worker, each protocol gateway, Browser Gateway, and web use separate Kubernetes
  Secrets. The API and gateways do not receive the authority issuer key; the worker
  does not receive email/calendar/provider-vision credentials; the web receives neither.
- Production should use External Secrets, Sealed Secrets, or a cloud secret manager.
- Skills require an Ed25519 signature from the configured trust root.
- JavaScript and Python production installs use frozen lockfiles; Python wheels are
  hash-verified. Dependabot separates major updates from compatible updates. CI runs
  lint, strict types, real frontend/backend tests, builds, and dependency audits.

## Operational controls

- `/healthz` is liveness and `/readyz` checks dependencies. `/metrics` includes HTTP,
  task, queue, capability-denial, and Receipt-replay metrics.
- Redis Streams jobs have visibility leases, cross-pod reclaim, bounded retries, and
  a dead-letter stream. Task execution uses an atomic pending-to-running claim.
- CI exercises enforcement against real Redis with AOF, including cross-process live
  tunnel revocation, fail-closed readiness during outage, and state recovery after
  restart. Kubernetes acceptance kills and recovers Postgres before running another
  verified task. The post-deploy smoke script separately verifies cluster egress policy.
- Ingress must terminate TLS and add HSTS. NetworkPolicies default-deny ingress.
