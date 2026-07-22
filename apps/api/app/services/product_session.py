"""Canonical Product Session specifications and revision instructions."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from app.db.models.task import TaskModel

PRODUCT_SPECIFICATION_SCHEMA = "loop.product-specification/v1"
FeedbackKind = Literal["implementation_fix", "product_decision"]


def initial_specification(goal: str, criteria: list[str]) -> dict[str, Any]:
    return {
        "schema": PRODUCT_SPECIFICATION_SCHEMA,
        "original_goal": goal.strip(),
        "required_acceptance_criteria": list(criteria),
        "feedback_history": [],
        "previous_contract_hash": None,
        "previous_receipt_hash": None,
    }


def revised_specification(
    previous: TaskModel,
    *,
    feedback: str,
    kind: FeedbackKind,
) -> dict[str, Any]:
    prior = (
        previous.product_specification if isinstance(previous.product_specification, dict) else {}
    )
    original_goal = str(prior.get("original_goal") or previous.goal).strip()
    history = [item for item in prior.get("feedback_history", []) if isinstance(item, dict)]
    delta = {
        "revision": (previous.product_revision or 1) + 1,
        "kind": kind,
        "feedback": feedback.strip(),
        "previous_task_id": str(previous.id),
    }
    required = [f"Product decision: {feedback.strip()}"]
    if kind == "implementation_fix":
        required = [
            (
                "The previous verified acceptance contract remains satisfied without regression "
                f"(contract {previous.contract_hash})."
            ),
            f"Regression requirement: {feedback.strip()}",
        ]
    return {
        "schema": PRODUCT_SPECIFICATION_SCHEMA,
        "original_goal": original_goal,
        "required_acceptance_criteria": required,
        "feedback_history": [*history, delta],
        "previous_contract_hash": previous.contract_hash,
        "previous_receipt_hash": previous.receipt_hash,
    }


def specification_hash(specification: dict[str, Any]) -> str:
    canonical = json.dumps(
        specification,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def revision_goal(specification: dict[str, Any], *, feedback: str, kind: FeedbackKind) -> str:
    original = str(specification.get("original_goal") or "").strip()
    required = [
        str(item).strip()
        for item in specification.get("required_acceptance_criteria", [])
        if str(item).strip()
    ]
    label = "implementation correction" if kind == "implementation_fix" else "product decision"
    criteria = "\n".join(f"- {item}" for item in required)
    return (
        f"Continue the prior verified delivery with this {label}.\n\n"
        f"Original product instruction:\n{original}\n\n"
        f"Feedback delta:\n{feedback.strip()}\n\n"
        f"Non-negotiable acceptance criteria for this revision:\n{criteria}"
    )


def required_revision_criteria(task: TaskModel) -> list[str]:
    if (task.product_revision or 0) <= 1 or not isinstance(task.product_specification, dict):
        return []
    return [
        str(item).strip()
        for item in task.product_specification.get("required_acceptance_criteria", [])
        if str(item).strip()
    ][:12]
