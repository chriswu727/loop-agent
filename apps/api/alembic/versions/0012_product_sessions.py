"""Add durable Product Sessions and immutable revision specifications.

Revision ID: 0012_product_sessions
Revises: 0011_loop_state_machine
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_product_sessions"
down_revision: str | None = "0011_loop_state_machine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _specification(goal: str, criteria: object) -> tuple[dict[str, object], str]:
    rubric = criteria if isinstance(criteria, list) else []
    specification: dict[str, object] = {
        "schema": "loop.product-specification/v1",
        "original_goal": goal.strip(),
        "required_acceptance_criteria": rubric,
        "feedback_history": [],
        "previous_contract_hash": None,
        "previous_receipt_hash": None,
    }
    canonical = json.dumps(
        specification, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return specification, hashlib.sha256(canonical).hexdigest()


def upgrade() -> None:
    op.create_table(
        "product_sessions",
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("project_id", sa.String(length=100), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_sessions")),
    )
    op.create_index(op.f("ix_product_sessions_owner_id"), "product_sessions", ["owner_id"])
    op.create_index(op.f("ix_product_sessions_project_id"), "product_sessions", ["project_id"])
    op.add_column("tasks", sa.Column("product_session_id", sa.Uuid(), nullable=True))
    op.add_column("tasks", sa.Column("product_revision", sa.Integer(), nullable=True))
    op.add_column("tasks", sa.Column("previous_revision_id", sa.Uuid(), nullable=True))
    op.add_column("tasks", sa.Column("superseded_by_id", sa.Uuid(), nullable=True))
    op.add_column("tasks", sa.Column("feedback_kind", sa.String(length=30), nullable=True))
    op.add_column("tasks", sa.Column("feedback_delta", sa.Text(), nullable=True))
    op.add_column("tasks", sa.Column("product_specification", sa.JSON(), nullable=True))
    op.add_column("tasks", sa.Column("specification_hash", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_tasks_product_session_id"), "tasks", ["product_session_id"])
    op.create_index(
        "uq_tasks_product_session_id_product_revision",
        "tasks",
        ["product_session_id", "product_revision"],
        unique=True,
    )

    connection = op.get_bind()
    product_sessions = sa.table(
        "product_sessions",
        sa.column("id", sa.Uuid()),
        sa.column("owner_id", sa.String()),
        sa.column("project_id", sa.String()),
    )
    tasks = sa.table(
        "tasks",
        sa.column("id", sa.Uuid()),
        sa.column("product_session_id", sa.Uuid()),
        sa.column("product_revision", sa.Integer()),
        sa.column("product_specification", sa.JSON()),
        sa.column("specification_hash", sa.String()),
    )
    rows = connection.execute(
        sa.text(
            "SELECT id, owner_id, project_id, goal, rubric FROM tasks "
            "WHERE project_source_path IS NOT NULL AND parent_id IS NULL"
        )
    ).mappings()
    for row in rows:
        session_id = uuid.uuid4()
        raw_rubric = row["rubric"]
        if isinstance(raw_rubric, str):
            try:
                raw_rubric = json.loads(raw_rubric)
            except ValueError:
                raw_rubric = []
        specification, digest = _specification(str(row["goal"]), raw_rubric)
        connection.execute(
            product_sessions.insert().values(
                id=session_id,
                owner_id=row["owner_id"],
                project_id=row["project_id"],
            )
        )
        connection.execute(
            tasks.update()
            .where(tasks.c.id == uuid.UUID(str(row["id"])))
            .values(
                product_session_id=session_id,
                product_revision=1,
                product_specification=specification,
                specification_hash=digest,
            )
        )


def downgrade() -> None:
    op.drop_index("uq_tasks_product_session_id_product_revision", table_name="tasks")
    op.drop_index(op.f("ix_tasks_product_session_id"), table_name="tasks")
    op.drop_column("tasks", "specification_hash")
    op.drop_column("tasks", "product_specification")
    op.drop_column("tasks", "feedback_delta")
    op.drop_column("tasks", "feedback_kind")
    op.drop_column("tasks", "superseded_by_id")
    op.drop_column("tasks", "previous_revision_id")
    op.drop_column("tasks", "product_revision")
    op.drop_column("tasks", "product_session_id")
    op.drop_index(op.f("ix_product_sessions_project_id"), table_name="product_sessions")
    op.drop_index(op.f("ix_product_sessions_owner_id"), table_name="product_sessions")
    op.drop_table("product_sessions")
