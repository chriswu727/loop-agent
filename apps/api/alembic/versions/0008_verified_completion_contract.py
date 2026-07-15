"""Add the verified-completion task contract and actual model provenance.

Revision ID: 0008_verified_completion
Revises: 0007_verified_changesets
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_verified_completion"
down_revision: str | None = "0007_verified_changesets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "criteria_source", sa.String(length=20), nullable=False, server_default="generated"
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "verification_mode", sa.String(length=20), nullable=False, server_default="judgment"
        ),
    )
    op.add_column(
        "tasks", sa.Column("required_checks", sa.JSON(), nullable=False, server_default="[]")
    )
    op.add_column(
        "tasks", sa.Column("baseline_checks", sa.JSON(), nullable=False, server_default="[]")
    )
    op.add_column(
        "tasks", sa.Column("executor_models", sa.JSON(), nullable=False, server_default="[]")
    )
    op.add_column("tasks", sa.Column("verifier_model", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "verifier_model")
    op.drop_column("tasks", "executor_models")
    op.drop_column("tasks", "baseline_checks")
    op.drop_column("tasks", "required_checks")
    op.drop_column("tasks", "verification_mode")
    op.drop_column("tasks", "criteria_source")
