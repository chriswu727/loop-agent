"""Prompt templates for the three LLM roles in the loop.

Kept apart from the orchestration so the wording can be tuned without touching
control flow. Each builder returns a ``(system, user)`` pair.
"""

from __future__ import annotations


def understand_prompts(goal: str) -> tuple[str, str]:
    system = (
        "You are a meticulous planner. Given a task, you define what a great "
        "result looks like as a short list of concrete, checkable success "
        "criteria — the rubric the work will be graded against. Criteria must be "
        "specific and observable, not vague aspirations."
    )
    user = (
        f"Task:\n{goal}\n\n"
        "Return ONLY a JSON array of 4 to 7 strings, each a single success "
        'criterion. Example: ["Opens with a clear one-sentence summary", '
        '"Uses concrete numbers, not adjectives"]. No prose, no markdown.'
    )
    return system, user


def produce_prompts(
    goal: str,
    rubric: list[str],
    best_artifact: str | None,
    last_critique: str | None,
) -> tuple[str, str]:
    system = (
        "You are an expert who produces excellent, finished work. You return the "
        "deliverable itself — no preamble, no explanation of what you did, no "
        "meta-commentary. Just the artifact."
    )
    criteria = "\n".join(f"- {c}" for c in rubric) or "- Fully and directly satisfies the task"
    if best_artifact and last_critique:
        user = (
            f"Task:\n{goal}\n\nSuccess criteria:\n{criteria}\n\n"
            f"Here is the current best draft:\n---\n{best_artifact}\n---\n\n"
            f"A reviewer raised these points:\n{last_critique}\n\n"
            "Produce a clearly improved version that fixes those points while "
            "keeping what already works. Return only the improved artifact."
        )
    else:
        user = (
            f"Task:\n{goal}\n\nSuccess criteria:\n{criteria}\n\n"
            "Produce the best possible first version. Return only the artifact."
        )
    return system, user


def critique_prompts(goal: str, rubric: list[str], artifact: str) -> tuple[str, str]:
    system = (
        "You are a demanding reviewer. You grade work against its rubric "
        "honestly and specifically. You never inflate scores; a 90+ means the "
        "work is genuinely excellent and nearly nothing could be improved."
    )
    criteria = "\n".join(f"- {c}" for c in rubric) or "- Fully and directly satisfies the task"
    user = (
        f"Task:\n{goal}\n\nSuccess criteria:\n{criteria}\n\n"
        f"Work to grade:\n---\n{artifact}\n---\n\n"
        "Return ONLY a JSON object with this exact shape:\n"
        '{"score": <integer 0-100>, "weaknesses": [<short strings>], '
        '"directives": [<short, actionable fixes for the next revision>]}\n'
        "No prose outside the JSON."
    )
    return system, user
