# ADR 0011: Product Session revision lineage

- Status: accepted
- Date: 2026-07-22

## Context

A verified task is a delivery, but developer feedback often arrives only after that
delivery is inspected. Treating the feedback as an unrelated retry loses the relationship
between specifications, contracts, evidence, and patches. Reusing `parent_id` would also
conflate product history with the existing sub-agent execution tree.

The change-set model adds another constraint: Apply writes a cumulative patch to the source
repository. If an older revision is applied while a newer one is being prepared, the newer
patch no longer has the clean base that makes Apply and Undo deterministic.

## Decision

Local-project tasks receive a separate Product Session identity and monotonically increasing
revision number. Each revision stores its previous task, feedback type and text,
content-addressed product specification, and superseding task.

A successor is created only when the current latest revision:

- completed with execution-backed acceptance and a valid Receipt;
- has a locked acceptance contract;
- still has a pending change set, or was applied and then undone; and
- points to the same clean source commit.

The runtime clones that immutable base and applies the prior Receipt-bound patch into the new
workspace. Work on v2 therefore starts from the exact verified v1 delivery, while v2's final
change set remains cumulative against the original base. Only the latest revision may Apply.

Implementation corrections retain a gate for the previous acceptance contract and add the
feedback as a regression requirement. Product decisions preserve the prior contract as
history but allow the new contract to supersede it, because a legitimate decision may
contradict an earlier requirement. Both meanings remain explicit in the specification and
Receipt.

Revision creation holds the existing cross-process source lock and a database row lock. A
unique `(product_session_id, product_revision)` index is the final race guard.

## Consequences

- Product history and sub-agent topology remain independent.
- v1 specifications, contracts, Receipts, workspaces, and change sets remain auditable after
  v2 exists.
- Apply and Undo keep one cumulative, base-relative patch model.
- A user must Undo an applied revision before continuing its lineage. Loop will not silently
  rewrite a dirty source tree.
- Direct visual/output diffs between adjacent revisions require a separate derived-delta
  view; the first slice exposes cumulative change sets and version navigation only.
