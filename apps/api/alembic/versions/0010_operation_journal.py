"""Add the crash-safe in-flight operation journal.

Revision ID: 0010_operation_journal
Revises: 0009_contract_draft
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_operation_journal"
down_revision: str | None = "0009_contract_draft"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("operation_journal", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "operation_journal")
