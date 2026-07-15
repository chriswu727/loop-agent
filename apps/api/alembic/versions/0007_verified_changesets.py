"""Add local project bindings and verified change-set lifecycle.

Revision ID: 0007_verified_changesets
Revises: 0006_authority_audit
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_verified_changesets"
down_revision: str | None = "0006_authority_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("project_source_path", sa.String(length=1024), nullable=True))
    op.add_column("tasks", sa.Column("project_relative_path", sa.String(length=500), nullable=True))
    op.add_column("tasks", sa.Column("project_base_commit", sa.String(length=64), nullable=True))
    op.add_column("tasks", sa.Column("project_base_branch", sa.String(length=255), nullable=True))
    op.add_column("tasks", sa.Column("change_state", sa.String(length=20), nullable=True))
    op.add_column("tasks", sa.Column("applied_patch_sha256", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "applied_patch_sha256")
    op.drop_column("tasks", "change_state")
    op.drop_column("tasks", "project_base_branch")
    op.drop_column("tasks", "project_base_commit")
    op.drop_column("tasks", "project_relative_path")
    op.drop_column("tasks", "project_source_path")
