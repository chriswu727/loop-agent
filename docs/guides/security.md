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
  the boundary. A signed skill may only narrow the task grant.
- `ToolExecutor.execute` is the enforcement choke point. Unknown tools and missing
  capabilities default-deny.
- `net.browser` does not grant `net.shell`; email, calendar, memory, vision, and
  delegation are independent grants.
- Side-effecting email/calendar actions and policy-classified commands pause through
  the persisted approval flow when approval is required.

## Sandbox and network

- Production uses `AGENT_SANDBOX=required` with the Kubernetes backend. If the
  backend or image is unavailable, the task fails; there is no host fallback.
- Each shell command runs in a fresh Job: non-root UID, read-only root filesystem,
  no service-account token, all Linux capabilities dropped, no privilege escalation,
  resource/time limits, and only the shared task volume plus `/tmp` mounted.
- Only the current task directory is mounted through a PVC `subPath`; other tenants'
  workspaces and memory are not visible. Production requires the sandbox image to be
  pinned by digest, and Receipts record that digest.
- Shell egress is denied by NetworkPolicy unless `net.shell` is granted. The optional
  host allowlist is enforced at the command/script policy layer; Kubernetes network
  isolation is currently boolean, not destination-aware. Use an egress proxy or CNI
  FQDN policy before treating the host allowlist as a network-layer guarantee.
- Local `preferred` mode may fall back to the host and labels the Receipt accordingly.
  Use `required` when containment is mandatory.
- Browser/email/calendar providers are disabled in the production manifest until an
  isolated provider gateway exists. Requests fail closed instead of running beside
  worker credentials.

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
- API/worker and web use separate Kubernetes Secrets so the web pod does not receive
  database or model credentials.
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
