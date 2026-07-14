"""Add versioned authority and receipt contract fields.

Revision ID: 0003_authority_receipt_contracts
Revises: 0002_egress_hosts
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_authority_receipt_contracts"
down_revision: str | None = "0002_egress_hosts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "authority_schema",
            sa.String(length=40),
            nullable=False,
            server_default="loop.capabilities/v1",
        ),
    )
    op.add_column("tasks", sa.Column("requested_capabilities", sa.JSON(), nullable=True))
    op.add_column(
        "tasks", sa.Column("resolved_capabilities", sa.JSON(), nullable=False, server_default="[]")
    )
    op.add_column(
        "tasks",
        sa.Column("use_vision", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("tasks", sa.Column("idempotency_key", sa.String(length=128), nullable=True))
    op.add_column(
        "tasks", sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("1"))
    )
    op.add_column("tasks", sa.Column("receipt_schema", sa.String(length=40), nullable=True))
    op.create_index(op.f("ix_tasks_idempotency_key"), "tasks", ["idempotency_key"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_tasks_idempotency_key"), table_name="tasks")
    op.drop_column("tasks", "receipt_schema")
    op.drop_column("tasks", "attempt")
    op.drop_column("tasks", "idempotency_key")
    op.drop_column("tasks", "use_vision")
    op.drop_column("tasks", "resolved_capabilities")
    op.drop_column("tasks", "requested_capabilities")
    op.drop_column("tasks", "authority_schema")
