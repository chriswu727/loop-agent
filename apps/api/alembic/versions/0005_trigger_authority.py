"""Scope triggers to owners/projects and persist typed capabilities.

Revision ID: 0005_trigger_authority
Revises: 0004_task_ownership
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_trigger_authority"
down_revision: str | None = "0004_task_ownership"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "triggers",
        sa.Column("owner_id", sa.String(length=255), nullable=False, server_default="local"),
    )
    op.add_column(
        "triggers",
        sa.Column("project_id", sa.String(length=100), nullable=False, server_default="default"),
    )
    op.add_column("triggers", sa.Column("capabilities", sa.JSON(), nullable=True))
    op.create_index(op.f("ix_triggers_owner_id"), "triggers", ["owner_id"], unique=False)
    op.create_index(op.f("ix_triggers_project_id"), "triggers", ["project_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_triggers_project_id"), table_name="triggers")
    op.drop_index(op.f("ix_triggers_owner_id"), table_name="triggers")
    op.drop_column("triggers", "capabilities")
    op.drop_column("triggers", "project_id")
    op.drop_column("triggers", "owner_id")
