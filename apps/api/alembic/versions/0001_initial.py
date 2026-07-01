"""initial schema: tasks + steps

Revision ID: 0001_initial
Revises:
Create Date: 2026-01-01 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("rubric", sa.JSON(), nullable=False),
        sa.Column("pending_question", sa.Text(), nullable=True),
        sa.Column("allowed_tools", sa.JSON(), nullable=True),
        sa.Column("allow_egress", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "require_approval", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("use_browser", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("use_email", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("pending_action", sa.JSON(), nullable=True),
        sa.Column("skill", sa.String(length=100), nullable=True),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("depth", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("chat_id", sa.String(length=64), nullable=True),
        sa.Column("max_steps", sa.Integer(), nullable=False),
        sa.Column("token_budget", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("verification_score", sa.Integer(), nullable=False),
        sa.Column("verified_by", sa.String(length=20), nullable=True),
        sa.Column("receipt_hash", sa.String(length=64), nullable=True),
        sa.Column("sandbox", sa.String(length=20), nullable=True),
        sa.Column("steps_used", sa.Integer(), nullable=False),
        sa.Column("tokens_used", sa.Integer(), nullable=False),
        sa.Column("workspace_path", sa.String(length=1024), nullable=True),
        sa.Column("stop_reason", sa.String(length=30), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tasks")),
    )
    op.create_index(op.f("ix_tasks_status"), "tasks", ["status"], unique=False)
    op.create_index(op.f("ix_tasks_parent_id"), "tasks", ["parent_id"], unique=False)
    op.create_index(op.f("ix_tasks_chat_id"), "tasks", ["chat_id"], unique=False)

    op.create_table(
        "steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("thought", sa.Text(), nullable=False),
        sa.Column("tool", sa.String(length=40), nullable=False),
        sa.Column("tool_args", sa.JSON(), nullable=False),
        sa.Column("observation", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=True),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            name=op.f("fk_steps_task_id_tasks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_steps")),
    )
    op.create_index(op.f("ix_steps_task_id"), "steps", ["task_id"], unique=False)

    op.create_table(
        "triggers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("fire_count", sa.Integer(), nullable=False),
        sa.Column("secret", sa.String(length=64), nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=True),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_steps", sa.Integer(), nullable=False),
        sa.Column("token_budget", sa.Integer(), nullable=False),
        sa.Column("allowed_tools", sa.JSON(), nullable=True),
        sa.Column("allow_egress", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "require_approval", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("skill", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_triggers")),
    )


def downgrade() -> None:
    op.drop_table("triggers")
    op.drop_index(op.f("ix_steps_task_id"), table_name="steps")
    op.drop_table("steps")
    op.drop_index(op.f("ix_tasks_chat_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_parent_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_status"), table_name="tasks")
    op.drop_table("tasks")
