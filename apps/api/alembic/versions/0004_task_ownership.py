"""Scope tasks and idempotency keys to an authenticated owner and project.

Revision ID: 0004_task_ownership
Revises: 0003_authority_receipt_contracts
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_task_ownership"
down_revision: str | None = "0003_authority_receipt_contracts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("owner_id", sa.String(length=255), nullable=False, server_default="local"),
    )
    op.add_column(
        "tasks",
        sa.Column("project_id", sa.String(length=100), nullable=False, server_default="default"),
    )
    op.create_index(op.f("ix_tasks_owner_id"), "tasks", ["owner_id"], unique=False)
    op.create_index(op.f("ix_tasks_project_id"), "tasks", ["project_id"], unique=False)
    with op.batch_alter_table("tasks") as batch:
        batch.create_unique_constraint("uq_tasks_owner_id", ["owner_id", "idempotency_key"])


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_constraint("uq_tasks_owner_id", type_="unique")
    op.drop_index(op.f("ix_tasks_project_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_owner_id"), table_name="tasks")
    op.drop_column("tasks", "project_id")
    op.drop_column("tasks", "owner_id")
