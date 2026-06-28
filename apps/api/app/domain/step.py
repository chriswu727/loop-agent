"""One step of the agent loop: a thought, the tool it chose, and what it observed."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Step:
    id: uuid.UUID
    task_id: uuid.UUID
    number: int  # 1-based step index within the task
    thought: str  # the agent's reasoning for this action
    tool: str  # which tool it called (write_file, run_command, finish, …)
    tool_args: dict[str, Any] = field(default_factory=dict)
    observation: str = ""  # what came back (file written, command output, …)
    status: str = "ok"  # ok | error | blocked
    tokens: int = 0  # tokens the planning call for this step consumed
    created_at: datetime | None = None
