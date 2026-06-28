"""Prompt templates for the agent's three LLM roles: understand, plan, verify.

Kept apart from the orchestration so wording can be tuned without touching
control flow. Each builder returns a ``(system, user)`` pair.
"""

from __future__ import annotations

from app.tools.registry import TOOL_SPECS


def understand_prompts(goal: str) -> tuple[str, str]:
    system = (
        "You are a meticulous planner. Given a task, you define what a finished, "
        "correct result looks like as a short list of concrete, checkable success "
        "criteria — the rubric the work will be graded against."
    )
    user = (
        f"Task:\n{goal}\n\n"
        "Return ONLY a JSON array of 3 to 6 strings, each a single success "
        'criterion. Example: ["A runnable script exists at solution.py", '
        '"Running it prints the first 10 Fibonacci numbers"]. No prose, no markdown.'
    )
    return system, user


def plan_prompts(
    goal: str,
    rubric: list[str],
    workspace_tree: str,
    history: str,
    steps_left: int,
    tokens_left: int,
) -> tuple[str, str]:
    system = (
        "You are an autonomous agent that completes a task by taking ONE action at "
        "a time. You work inside a sandboxed workspace directory. You think, act, "
        "observe the result, then decide the next action — repeating until the task "
        "is genuinely done.\n\n"
        "Available tools:\n"
        f"{TOOL_SPECS}\n\n"
        "Rules:\n"
        "- Respond with ONE JSON object and nothing else: "
        '{\"thought\": \"...\", \"tool\": \"<tool>\", \"args\": {...}}.\n'
        "- Take exactly one action per turn. Verify your own work (e.g. run the "
        "code you wrote) before calling finish.\n"
        "- Call finish ONLY when every success criterion is demonstrably met, with "
        "evidence in your observations.\n"
        "- Paths are relative to the workspace. Keep commands simple and safe."
    )
    criteria = "\n".join(f"- {c}" for c in rubric) or "- Fully satisfies the task"
    user = (
        f"Goal:\n{goal}\n\n"
        f"Success criteria:\n{criteria}\n\n"
        f"Workspace files:\n{workspace_tree}\n\n"
        f"What you have done so far:\n{history or '(nothing yet)'}\n\n"
        f"Budget left: {steps_left} steps, ~{tokens_left} tokens.\n\n"
        "Decide the single next action. Respond with the JSON object only."
    )
    return system, user


def verify_prompts(
    goal: str, rubric: list[str], summary: str, workspace_tree: str
) -> tuple[str, str]:
    system = (
        "You are a demanding verifier. You decide whether an autonomous agent has "
        "actually completed a task, judging only by evidence — not by the agent's "
        "claims. You never rubber-stamp."
    )
    criteria = "\n".join(f"- {c}" for c in rubric) or "- Fully satisfies the task"
    user = (
        f"Goal:\n{goal}\n\nSuccess criteria:\n{criteria}\n\n"
        f"Workspace files:\n{workspace_tree}\n\n"
        f"The agent says it is done:\n{summary}\n\n"
        "Return ONLY a JSON object: "
        '{\"score\": <0-100>, \"met\": <true|false>, '
        '\"missing\": [<short strings: what is not yet satisfied>]}. '
        "met=true only if every criterion is clearly satisfied."
    )
    return system, user
