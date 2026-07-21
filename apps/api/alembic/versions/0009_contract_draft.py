"""Add the pre-mutation repository contract draft.

Revision ID: 0009_contract_draft
Revises: 0008_verified_completion
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_contract_draft"
down_revision: str | None = "0008_verified_completion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("contract_draft", sa.JSON(), nullable=True))
    op.add_column("tasks", sa.Column("contract_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "tasks",
        sa.Column(
            "contract_status",
            sa.String(length=20),
            nullable=False,
            server_default="not_required",
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "contract_status")
    op.drop_column("tasks", "contract_hash")
    op.drop_column("tasks", "contract_draft")
