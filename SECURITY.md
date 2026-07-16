# Security policy

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting flow:

<https://github.com/chriswu727/loop-agent/security/advisories/new>

Do not include live credentials, private user data, or a working exploit in a public
issue. Include the affected revision, deployment mode, reproduction steps, expected
boundary, and observed impact. Reports will be acknowledged as soon as possible; a
fix timeline depends on severity and whether coordinated disclosure is required.

Only the latest `0.1.x` release and current `main` receive security fixes.

## Threat model

Loop assumes all of the following may be malicious or incorrect:

- the planner or verifier model;
- uploaded files, web pages, email/calendar content, memory, and tool output;
- task-authored shell commands;
- third-party skill bundles until their signature and declared capabilities verify;
- public network destinations and their DNS responses.

The deployment operator, control-plane configuration, trust roots, signing keys,
container/Kubernetes runtime, database, Redis, and artifact storage are trusted. A
host or cluster-admin compromise is outside the isolation boundary.

## Enforced boundaries

- Every tool call passes through a server-side capability envelope. Unknown or
  undeclared tools fail closed.
- Step and token limits are clamped server-side; retries and verification consume the
  same budget.
- Production command execution requires short-lived non-root Kubernetes Jobs with a
  read-only root filesystem, dropped capabilities, no service-account token, bounded
  resources, and an explicit workspace mount.
- Network access is separate from shell/browser access. Allowed hosts are declared
  before the run and enforced by an authority-token-verifying proxy that rejects
  private targets and pins DNS resolution.
- Provider credentials live in isolated protocol gateways in the production profile.
  The task command environment is scrubbed and observations are redacted before
  persistence or API return.
- Task steps are hash-chained. Receipts bind the contract, re-executed checks, output
  manifest, runtime/model provenance, authority, and ledger head. Unsigned development
  Receipts are tamper-evident; signed Receipts additionally authenticate their origin.
- Subject and project ownership scope tasks, files, memory, triggers, idempotency keys,
  change sets, and Receipts.

## Deployment modes

| Mode                    | Command boundary                                 | Intended use                  |
| ----------------------- | ------------------------------------------------ | ----------------------------- |
| Kubernetes production   | One short-lived Job per command; fail-closed     | Untrusted or shared workloads |
| Docker local/desktop    | Ephemeral container with explicit mounts/network | Recommended laptop use        |
| Inline demo/development | Command runs on the host under path/tool policy  | Trusted demo tasks only       |

Inline mode is not process isolation. It is labeled `reduced isolation` in the UI and
must not be used for untrusted model output on a machine containing sensitive data.

Local Sibyl and Argus MCP subprocesses also run outside the task container. They are
opt-in development integrations, visibly disclosed in authority metadata, and refused
when production mode disables host providers.

## Residual risks

- Prompt/data separation reduces accidental instruction following but does not prove
  that a model will never be influenced by malicious content. Runtime enforcement is
  the security boundary.
- A user-approved capability can still be used harmfully inside its declared scope.
  Review destination lists, approval prompts, diffs, and Receipts.
- A compromised container runtime, Kubernetes node, cluster administrator, database,
  Redis instance, artifact volume, or signing key can violate the stated guarantees.
- DNS rebinding defenses and destination checks reduce SSRF risk but do not make an
  approved third-party service trustworthy.
- Receipt integrity proves what Loop recorded and re-executed under the captured
  environment. It does not prove semantic correctness beyond the acceptance contract
  and checks, or reproducibility after dependencies and external services change.
- Browser sessions are currently pod-local and single-replica. A browser gateway
  restart loses active sessions; durable browser migration is not implemented.

## Production checklist

- Set `ENVIRONMENT=production`, `AUTH_REQUIRED=true`, a non-default `SECRET_KEY`, and
  an immutable sandbox image digest.
- Configure Receipt and authority signing keys outside the image; expose only verifier
  public keys to gateways and proxies.
- Keep host providers disabled and configure the separate browser/email/calendar/
  vision gateways plus the destination-enforcing egress proxy.
- Use durable Postgres, Redis, and artifact storage with backups, access controls,
  encryption, and monitoring appropriate to the deployment.
- Terminate TLS at ingress, restrict admin endpoints, rotate secrets, and review audit
  and revocation retention.
- Run `make enforcement-acceptance` and `make k8s-deployment-acceptance` against the
  exact revision before promotion.
