"""Add tasks.egress_hosts — an optional per-task egress allowlist.

Revision ID: 0002_egress_hosts
Revises: 0001_initial
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_egress_hosts"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("egress_hosts", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "egress_hosts")
