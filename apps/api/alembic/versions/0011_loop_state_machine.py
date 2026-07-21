"""Persist the explicit loop state and latest transition reason.

Revision ID: 0011_loop_state_machine
Revises: 0010_operation_journal
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_loop_state_machine"
down_revision: str | None = "0010_operation_journal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("loop_state", sa.String(length=30), nullable=False, server_default="queued"),
    )
    op.add_column("tasks", sa.Column("transition_reason", sa.Text(), nullable=True))
    op.add_column(
        "tasks",
        sa.Column("transition_sequence", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_tasks_loop_state", "tasks", ["loop_state"], unique=False)
    op.execute(
        """
        UPDATE tasks
        SET loop_state = CASE
                WHEN status = 'pending' THEN 'queued'
                WHEN status = 'running' THEN 'preparing'
                WHEN status = 'awaiting_input' THEN 'awaiting_input'
                WHEN status = 'completed' AND stop_reason = 'goal_achieved' THEN 'completed'
                WHEN status = 'completed' THEN 'stopped'
                WHEN status = 'stopped' THEN 'stopped'
                WHEN status = 'cancelled' THEN 'cancelled'
                WHEN status = 'failed' THEN 'failed'
                ELSE 'failed'
            END,
        status = CASE
                WHEN status = 'completed' AND COALESCE(stop_reason, '') <> 'goal_achieved'
                    THEN 'stopped'
                WHEN status IN (
                    'pending', 'running', 'awaiting_input', 'completed',
                    'stopped', 'cancelled', 'failed'
                ) THEN status
                ELSE 'failed'
            END,
        transition_reason = 'migrated_from_task_status'
        """
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_loop_state", table_name="tasks")
    op.drop_column("tasks", "transition_sequence")
    op.drop_column("tasks", "transition_reason")
    op.drop_column("tasks", "loop_state")
