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
  hosts, and a short expiry. Provider Gateway and proxy processes have only the
  public verifier, so a compromised enforcement service cannot mint wider grants.
- Verification uses the token's derived Ed25519 `kid` against a configured keyring.
  A terminal run sends a separately audience-bound control token to both enforcement
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
- Browser/email/calendar/vision provider credentials exist only in the Provider
  Gateway. The worker calls it with the short-lived grant; production requests fail
  closed if the gateway is missing or does not expose every granted capability.

### Exact guarantee boundaries

- Shell containers/Jobs have no direct external route: destination enforcement is a
  network-layer property of the sandbox namespace plus proxy.
- Browser navigation is checked by the Provider Gateway and routed through the same
  authenticated proxy. A gateway-local loopback relay refreshes short-lived proxy
  authority without exposing it to Chromium, and the proxy closes established
  connections when that authority expires. The gateway pod also needs direct
  protocol egress for SMTP/IMAP/CalDAV/vision APIs; standard Kubernetes NetworkPolicy
  cannot express DNS-name policy for those connections. Deployments that need equivalent L4
  separation should split browser and protocol providers into separate gateways or
  use a CNI/service mesh with FQDN policy.
- Proxy audit is a bounded SQLite WAL on a dedicated persistent volume, so a proxy
  restart does not erase events waiting for the worker to embed them in a task and
  Receipt. The base deployment intentionally has one replica because this local
  store is not a horizontally shared log. Use an external append-only audit sink if
  the deployment needs multi-replica proxy HA or compliance-ledger retention.
- Revocation prevents new calls and closes browser/proxy connections, but cannot roll
  back a completed side effect or guarantee interruption of an SMTP/CalDAV operation
  already executing in an upstream library. API cancellation is observed between
  agent steps, then the worker publishes the signed revocation at the terminal boundary.

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
- API, worker, Provider Gateway, and web use separate Kubernetes Secrets. The API and
  gateway do not receive the authority issuer key; the worker does not receive
  email/calendar/provider-vision credentials; the web receives neither.
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
- Ingress must terminate TLS and add HSTS. NetworkPolicies default-deny ingress.
