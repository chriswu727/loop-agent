"""Persist runtime provider and destination enforcement decisions.

Revision ID: 0006_authority_audit
Revises: 0005_trigger_authority
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_authority_audit"
down_revision: str | None = "0005_trigger_authority"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("authority_audit", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column("triggers", sa.Column("egress_hosts", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("triggers", "egress_hosts")
    op.drop_column("tasks", "authority_audit")
