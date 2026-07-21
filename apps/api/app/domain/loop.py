"""The authoritative task-loop transition policy."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Protocol

from app.domain.task import StopReason, TaskStatus


class LoopState(enum.StrEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    UNDERSTANDING = "understanding"
    PLANNING = "planning"
    ACTING = "acting"
    VERIFYING = "verifying"
    AWAITING_INPUT = "awaiting_input"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    STOPPED = "stopped"
    CANCELLED = "cancelled"
    FAILED = "failed"


class LoopEvent(enum.StrEnum):
    CLAIM = "claim"
    RECOVER = "recover"
    CONTRACT_REQUIRED = "contract_required"
    CONTRACT_READY = "contract_ready"
    RUBRIC_REQUIRED = "rubric_required"
    PREPARATION_COMPLETED = "preparation_completed"
    RUBRIC_READY = "rubric_ready"
    ACTION_SELECTED = "action_selected"
    ACTION_RECORDED = "action_recorded"
    VERIFICATION_REQUESTED = "verification_requested"
    VERIFICATION_REJECTED = "verification_rejected"
    VERIFICATION_ACCEPTED = "verification_accepted"
    INPUT_REQUIRED = "input_required"
    APPROVAL_REQUIRED = "approval_required"
    USER_RESPONDED = "user_responded"
    LIMIT_REACHED = "limit_reached"
    CANCEL = "cancel"
    FAIL = "fail"


class InvalidLoopTransitionError(ValueError):
    pass


class TransitionTarget(Protocol):
    loop_state: str
    transition_reason: str | None
    transition_sequence: int
    status: str
    stop_reason: str | None


@dataclass(frozen=True, slots=True)
class LoopTransition:
    sequence: int
    source: LoopState
    target: LoopState
    event: LoopEvent
    reason: str


_WORKING = {
    LoopState.PREPARING,
    LoopState.UNDERSTANDING,
    LoopState.PLANNING,
    LoopState.ACTING,
    LoopState.VERIFYING,
}
_ACTIVE = {LoopState.QUEUED, *_WORKING, LoopState.AWAITING_INPUT, LoopState.AWAITING_APPROVAL}

_RULES: dict[tuple[LoopState, LoopEvent], LoopState] = {
    (LoopState.QUEUED, LoopEvent.CLAIM): LoopState.PREPARING,
    (LoopState.PREPARING, LoopEvent.CONTRACT_REQUIRED): LoopState.UNDERSTANDING,
    (LoopState.UNDERSTANDING, LoopEvent.CONTRACT_READY): LoopState.PLANNING,
    (LoopState.PREPARING, LoopEvent.RUBRIC_REQUIRED): LoopState.UNDERSTANDING,
    (LoopState.PREPARING, LoopEvent.PREPARATION_COMPLETED): LoopState.PLANNING,
    (LoopState.UNDERSTANDING, LoopEvent.RUBRIC_READY): LoopState.PLANNING,
    (LoopState.PLANNING, LoopEvent.ACTION_SELECTED): LoopState.ACTING,
    (LoopState.ACTING, LoopEvent.ACTION_RECORDED): LoopState.PLANNING,
    (LoopState.PLANNING, LoopEvent.VERIFICATION_REQUESTED): LoopState.VERIFYING,
    (LoopState.VERIFYING, LoopEvent.VERIFICATION_REJECTED): LoopState.PLANNING,
    (LoopState.VERIFYING, LoopEvent.VERIFICATION_ACCEPTED): LoopState.COMPLETED,
    (LoopState.PREPARING, LoopEvent.INPUT_REQUIRED): LoopState.AWAITING_INPUT,
    (LoopState.UNDERSTANDING, LoopEvent.INPUT_REQUIRED): LoopState.AWAITING_INPUT,
    (LoopState.ACTING, LoopEvent.INPUT_REQUIRED): LoopState.AWAITING_INPUT,
    (LoopState.ACTING, LoopEvent.APPROVAL_REQUIRED): LoopState.AWAITING_APPROVAL,
    (LoopState.AWAITING_INPUT, LoopEvent.USER_RESPONDED): LoopState.QUEUED,
    (LoopState.AWAITING_APPROVAL, LoopEvent.USER_RESPONDED): LoopState.QUEUED,
}
for state in _WORKING:
    _RULES[(state, LoopEvent.RECOVER)] = LoopState.PREPARING
    _RULES[(state, LoopEvent.LIMIT_REACHED)] = LoopState.STOPPED
for state in _ACTIVE:
    _RULES[(state, LoopEvent.CANCEL)] = LoopState.CANCELLED
    _RULES[(state, LoopEvent.FAIL)] = LoopState.FAILED

_STATUS_BY_STATE = {
    LoopState.QUEUED: TaskStatus.PENDING,
    LoopState.PREPARING: TaskStatus.RUNNING,
    LoopState.UNDERSTANDING: TaskStatus.RUNNING,
    LoopState.PLANNING: TaskStatus.RUNNING,
    LoopState.ACTING: TaskStatus.RUNNING,
    LoopState.VERIFYING: TaskStatus.RUNNING,
    LoopState.AWAITING_INPUT: TaskStatus.AWAITING_INPUT,
    LoopState.AWAITING_APPROVAL: TaskStatus.AWAITING_INPUT,
    LoopState.COMPLETED: TaskStatus.COMPLETED,
    LoopState.STOPPED: TaskStatus.STOPPED,
    LoopState.CANCELLED: TaskStatus.CANCELLED,
    LoopState.FAILED: TaskStatus.FAILED,
}


class LoopTransitionPolicy:
    @staticmethod
    def claimable_states() -> frozenset[LoopState]:
        return frozenset({LoopState.QUEUED, *_WORKING})

    @staticmethod
    def allowed_events(state: LoopState) -> frozenset[LoopEvent]:
        return frozenset(event for source, event in _RULES if source is state)

    @staticmethod
    def status_for(state: LoopState) -> TaskStatus:
        return _STATUS_BY_STATE[state]

    def apply(
        self,
        task: TransitionTarget,
        event: LoopEvent,
        reason: str,
        *,
        stop_reason: StopReason | None = None,
    ) -> LoopTransition:
        try:
            source = LoopState(task.loop_state)
        except ValueError as exc:
            raise InvalidLoopTransitionError(f"Unknown loop state {task.loop_state!r}") from exc
        target = _RULES.get((source, event))
        if target is None:
            raise InvalidLoopTransitionError(f"{event.value} is not allowed from {source.value}")
        if event is LoopEvent.LIMIT_REACHED and stop_reason not in {
            StopReason.MAX_STEPS,
            StopReason.BUDGET_EXHAUSTED,
            StopReason.STUCK,
        }:
            raise InvalidLoopTransitionError("A limit transition needs a bounded stop reason")

        sequence = task.transition_sequence + 1
        transition = LoopTransition(
            sequence=sequence,
            source=source,
            target=target,
            event=event,
            reason=reason.strip() or event.value,
        )
        task.loop_state = target.value
        task.transition_reason = transition.reason
        task.transition_sequence = sequence
        task.status = _STATUS_BY_STATE[target].value
        if target is LoopState.COMPLETED:
            task.stop_reason = StopReason.GOAL_ACHIEVED.value
        elif target is LoopState.STOPPED:
            assert stop_reason is not None
            task.stop_reason = stop_reason.value
        elif target is LoopState.CANCELLED:
            task.stop_reason = StopReason.CANCELLED.value
        elif target is LoopState.FAILED:
            task.stop_reason = StopReason.ERROR.value
        else:
            task.stop_reason = None
        return transition
