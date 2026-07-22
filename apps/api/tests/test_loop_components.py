from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.domain.task import StopReason, TaskStatus
from app.services.loop import (
    ActionDispatchPolicy,
    ContextBudget,
    DecisionParser,
    DelegationPolicy,
    DispatchKind,
    HistoryWindow,
    InvalidLoopTransitionError,
    LoopEvent,
    LoopState,
    LoopTransitionPolicy,
    VerificationPolicy,
    extract_json,
)
from app.services.verification import CheckResult


@dataclass
class TaskState:
    loop_state: str = LoopState.QUEUED.value
    transition_reason: str | None = None
    transition_sequence: int = 0
    status: str = TaskStatus.PENDING.value
    stop_reason: str | None = None


def transition(
    policy: LoopTransitionPolicy,
    task: TaskState,
    event: LoopEvent,
    *,
    stop_reason: StopReason | None = None,
) -> None:
    policy.apply(task, event, f"because:{event.value}", stop_reason=stop_reason)


def test_fresh_loop_success_path_is_explicit() -> None:
    policy = LoopTransitionPolicy()
    task = TaskState()
    for event in (
        LoopEvent.CLAIM,
        LoopEvent.RUBRIC_REQUIRED,
        LoopEvent.RUBRIC_READY,
        LoopEvent.ACTION_SELECTED,
        LoopEvent.ACTION_RECORDED,
        LoopEvent.VERIFICATION_REQUESTED,
        LoopEvent.VERIFICATION_ACCEPTED,
    ):
        transition(policy, task, event)

    assert task.loop_state == LoopState.COMPLETED.value
    assert task.status == TaskStatus.COMPLETED.value
    assert task.stop_reason == StopReason.GOAL_ACHIEVED.value
    assert task.transition_sequence == 7
    assert task.transition_reason == "because:verification_accepted"


def test_contract_compilation_success_path_is_explicit() -> None:
    policy = LoopTransitionPolicy()
    task = TaskState()
    for event in (
        LoopEvent.CLAIM,
        LoopEvent.CONTRACT_REQUIRED,
        LoopEvent.CONTRACT_READY,
        LoopEvent.ACTION_SELECTED,
        LoopEvent.ACTION_RECORDED,
        LoopEvent.VERIFICATION_REQUESTED,
        LoopEvent.VERIFICATION_ACCEPTED,
    ):
        transition(policy, task, event)

    assert task.loop_state == LoopState.COMPLETED.value
    assert task.status == TaskStatus.COMPLETED.value


@pytest.mark.parametrize(
    ("pause_event", "pause_state"),
    [
        (LoopEvent.INPUT_REQUIRED, LoopState.AWAITING_INPUT),
        (LoopEvent.APPROVAL_REQUIRED, LoopState.AWAITING_APPROVAL),
    ],
)
def test_every_pause_path_resumes_through_queue(
    pause_event: LoopEvent, pause_state: LoopState
) -> None:
    policy = LoopTransitionPolicy()
    task = TaskState()
    transition(policy, task, LoopEvent.CLAIM)
    transition(policy, task, LoopEvent.PREPARATION_COMPLETED)
    transition(policy, task, LoopEvent.ACTION_SELECTED)
    transition(policy, task, pause_event)
    assert task.loop_state == pause_state.value
    assert task.status == TaskStatus.AWAITING_INPUT.value

    transition(policy, task, LoopEvent.USER_RESPONDED)
    transition(policy, task, LoopEvent.CLAIM)
    assert task.loop_state == LoopState.PREPARING.value
    assert task.status == TaskStatus.RUNNING.value


@pytest.mark.parametrize(
    ("source", "event", "target"),
    [
        (LoopState.PREPARING, LoopEvent.INPUT_REQUIRED, LoopState.AWAITING_INPUT),
        (LoopState.UNDERSTANDING, LoopEvent.INPUT_REQUIRED, LoopState.AWAITING_INPUT),
        (LoopState.ACTING, LoopEvent.INPUT_REQUIRED, LoopState.AWAITING_INPUT),
        (LoopState.ACTING, LoopEvent.APPROVAL_REQUIRED, LoopState.AWAITING_APPROVAL),
        (LoopState.AWAITING_INPUT, LoopEvent.USER_RESPONDED, LoopState.QUEUED),
        (LoopState.AWAITING_APPROVAL, LoopEvent.USER_RESPONDED, LoopState.QUEUED),
    ],
)
def test_every_human_resumable_transition_is_explicit(
    source: LoopState,
    event: LoopEvent,
    target: LoopState,
) -> None:
    policy = LoopTransitionPolicy()
    task = TaskState(loop_state=source.value, status=policy.status_for(source).value)
    transition(policy, task, event)
    assert task.loop_state == target.value
    assert task.status == policy.status_for(target).value


@pytest.mark.parametrize(
    "source",
    [
        LoopState.PREPARING,
        LoopState.UNDERSTANDING,
        LoopState.PLANNING,
        LoopState.ACTING,
        LoopState.VERIFYING,
    ],
)
def test_every_working_state_can_recover_to_preparing(source: LoopState) -> None:
    policy = LoopTransitionPolicy()
    task = TaskState(loop_state=source.value, status=TaskStatus.RUNNING.value)
    transition(policy, task, LoopEvent.RECOVER)
    assert task.loop_state == LoopState.PREPARING.value
    assert task.status == TaskStatus.RUNNING.value
    assert source in policy.claimable_states()


@pytest.mark.parametrize(
    "source",
    [
        LoopState.PREPARING,
        LoopState.UNDERSTANDING,
        LoopState.PLANNING,
        LoopState.ACTING,
        LoopState.VERIFYING,
    ],
)
@pytest.mark.parametrize(
    "reason", [StopReason.MAX_STEPS, StopReason.BUDGET_EXHAUSTED, StopReason.STUCK]
)
def test_every_working_state_has_each_bounded_terminal_path(
    source: LoopState, reason: StopReason
) -> None:
    policy = LoopTransitionPolicy()
    task = TaskState(loop_state=source.value, status=TaskStatus.RUNNING.value)
    transition(policy, task, LoopEvent.LIMIT_REACHED, stop_reason=reason)
    assert task.loop_state == LoopState.STOPPED.value
    assert task.status == TaskStatus.STOPPED.value
    assert task.stop_reason == reason.value


@pytest.mark.parametrize(
    "source",
    [
        LoopState.QUEUED,
        LoopState.PREPARING,
        LoopState.UNDERSTANDING,
        LoopState.PLANNING,
        LoopState.ACTING,
        LoopState.VERIFYING,
        LoopState.AWAITING_INPUT,
        LoopState.AWAITING_APPROVAL,
    ],
)
@pytest.mark.parametrize(
    ("event", "target", "status", "stop"),
    [
        (LoopEvent.CANCEL, LoopState.CANCELLED, TaskStatus.CANCELLED, StopReason.CANCELLED),
        (LoopEvent.FAIL, LoopState.FAILED, TaskStatus.FAILED, StopReason.ERROR),
    ],
)
def test_every_active_state_has_cancel_and_failure_paths(
    source: LoopState,
    event: LoopEvent,
    target: LoopState,
    status: TaskStatus,
    stop: StopReason,
) -> None:
    policy = LoopTransitionPolicy()
    task = TaskState(loop_state=source.value, status=policy.status_for(source).value)
    transition(policy, task, event)
    assert task.loop_state == target.value
    assert task.status == status.value
    assert task.stop_reason == stop.value


def test_invalid_or_underspecified_transitions_fail_closed() -> None:
    policy = LoopTransitionPolicy()
    with pytest.raises(InvalidLoopTransitionError, match="not allowed"):
        transition(policy, TaskState(), LoopEvent.VERIFICATION_ACCEPTED)
    task = TaskState(loop_state=LoopState.PLANNING.value, status=TaskStatus.RUNNING.value)
    with pytest.raises(InvalidLoopTransitionError, match="bounded stop reason"):
        transition(policy, task, LoopEvent.LIMIT_REACHED)


def test_transition_table_has_no_unreachable_noninitial_state() -> None:
    policy = LoopTransitionPolicy()
    targets = {
        target
        for source in LoopState
        for event in policy.allowed_events(source)
        if (target := _target(policy, source, event)) is not None
    }
    assert set(LoopState) - {LoopState.QUEUED} <= targets


def _target(policy: LoopTransitionPolicy, source: LoopState, event: LoopEvent) -> LoopState | None:
    task = TaskState(loop_state=source.value, status=policy.status_for(source).value)
    try:
        policy.apply(
            task,
            event,
            event.value,
            stop_reason=StopReason.STUCK if event is LoopEvent.LIMIT_REACHED else None,
        )
    except InvalidLoopTransitionError:
        return None
    return LoopState(task.loop_state)


def test_decision_parser_prefers_final_json_and_enforces_tool_registry() -> None:
    assert extract_json('{"thought":"draft"}\nthen {"tool":"finish","args":{}}')["tool"] == (
        "finish"
    )
    parser = DecisionParser()
    parsed = parser.parse(
        'reasoning {not: json}\n{"thought":"done","tool":"finish","args":{"x":1}}',
        valid_tools={"finish"},
    )
    assert (parsed.thought, parsed.tool, parsed.args) == ("done", "finish", {"x": 1})
    assert parser.parse('{"tool":"unknown"}', valid_tools={"finish"}).tool is None


def test_context_budget_preserves_verification_reserve() -> None:
    budget = ContextBudget.allocate(total=4_000, used=2_900, reserve_cap=800)
    assert budget.verification_reserve == 800
    assert budget.remaining == 1_100
    assert budget.planning == 300
    assert budget.verification_after(300) == 800


def test_history_window_compacts_after_four_recent_steps() -> None:
    assert HistoryWindow().recent_steps == 4


def test_dispatch_policy_routes_invalid_blocked_approval_and_execute() -> None:
    policy = ActionDispatchPolicy()
    common = {
        "valid_tools": {"run_command", "write_file"},
        "guard_block": None,
        "repeated_write_count": 0,
        "approval_reason": None,
        "last_write_path": None,
    }
    assert policy.route(None, **common).kind is DispatchKind.INVALID
    assert policy.route("run_command", **{**common, "guard_block": "duplicate"}).kind is (
        DispatchKind.BLOCKED
    )
    assert (
        policy.route("run_command", **{**common, "approval_reason": "unsafe command"}).kind
        is DispatchKind.APPROVAL
    )
    assert policy.route("run_command", **common).kind is DispatchKind.EXECUTE


def test_delegation_policy_refuses_flooring_and_caps_child() -> None:
    policy = DelegationPolicy(minimum_budget=1_000, default_steps=8, max_steps=20)
    assert policy.allocate({}, 999) is None
    allocation = policy.allocate({"token_budget": 9_000, "max_steps": 99}, 2_000)
    assert allocation is not None
    assert (allocation.token_budget, allocation.max_steps) == (2_000, 20)


def test_verification_policy_requires_execution_evidence_in_strict_mode() -> None:
    policy = VerificationPolicy()
    result = CheckResult(
        kind="command",
        target="pytest",
        passed=True,
        evidence="exit code 0",
        criterion_ids=("criterion-001",),
    )
    accepted = policy.evaluate(
        {"score": 95, "met": True, "missing": []},
        [result],
        criterion_count=1,
        acceptance_score=80,
        strict=True,
        contract_substantiation_authoritative=True,
    )
    assert accepted.accepted is True
    assert accepted.verified_by == "execution"

    rejected = policy.evaluate(
        {"score": 95, "met": True, "missing": []},
        [],
        criterion_count=1,
        acceptance_score=80,
        strict=True,
        contract_substantiation_authoritative=False,
    )
    assert rejected.accepted is False
    assert "mapped execution check" in " ".join(rejected.missing)
