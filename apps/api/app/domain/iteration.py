"""One pass of the agent loop: a draft, its critique, and what it cost."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class Iteration:
    id: uuid.UUID
    task_id: uuid.UUID
    number: int  # 1-based pass index within the task
    artifact: str
    score: int  # 0-100, the critic's grade of this artifact
    critique: str  # concrete weaknesses + directives for the next pass
    tokens: int  # tokens this pass consumed (produce + critique)
    created_at: datetime | None = None
