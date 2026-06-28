# Security

Defense in depth. The hooks exist even though there's no auth logic yet.

## Secrets

- Never commit secrets. `.env` is gitignored; `secret.example.yaml` is a template.
- In production use **Sealed Secrets**, **External Secrets Operator**, or your
  cloud secret manager. Remove the example Secret from the base.
- Rotate `SECRET_KEY` and database credentials regularly.

## AuthN / AuthZ

- JWT verification and a `CurrentSubject` dependency are stubbed in
  `app/core/security.py` and `app/api/v1/deps.py`. Wire them to your IdP
  (Auth0/Clerk/Cognito/your own). Protect a route with
  `dependencies=[Depends(get_current_subject)]`.
- Add authorization checks in the **service** layer (it knows the domain).

## Transport & headers

- TLS terminates at the ingress; the ingress should add HSTS.
- Next.js sets `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
  and a restrictive `Permissions-Policy` (see `next.config.mjs`).

## Containers & cluster

- Images run as **non-root**, drop all Linux capabilities, disable privilege
  escalation, and use `readOnlyRootFilesystem` (api/worker) with a `/tmp` emptyDir.
- **NetworkPolicies** default-deny ingress; only required flows are allowed.
- `automountServiceAccountToken: false` — pods don't get API credentials they
  don't need.

## Input & abuse

- Every request body/query is validated by Pydantic before your code runs.
- A Redis-backed rate-limit dependency guards expensive routes.

## Dependencies

- Dependabot (weekly) for npm, pip, Actions, and Docker.
- Consider adding CodeQL and image scanning (e.g. Trivy) to CI.
