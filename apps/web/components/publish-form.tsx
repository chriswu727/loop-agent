'use client';

import type { LimitDefaults } from '@repo/api-contract';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { ApiError, tasksApi } from '@/lib/api-client';

const FALLBACK: LimitDefaults = {
  max_iterations_default: 6,
  max_iterations_cap: 15,
  token_budget_default: 60000,
  token_budget_cap: 200000,
  target_score_default: 90,
};

const EXAMPLES = [
  'Write a concise, friendly onboarding email for a SaaS free-trial signup.',
  'Draft a clear product one-pager for a personal expense tracker.',
  'Write a Python function that parses an ISO-8601 duration into seconds, with docstring and edge cases.',
];

export function PublishForm({ defaults }: { defaults: LimitDefaults | null }) {
  const d = defaults ?? FALLBACK;
  const router = useRouter();

  const [goal, setGoal] = useState('');
  const [maxIterations, setMaxIterations] = useState(d.max_iterations_default);
  const [tokenBudget, setTokenBudget] = useState(d.token_budget_default);
  const [targetScore, setTargetScore] = useState(d.target_score_default);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (goal.trim().length < 4 || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const task = await tasksApi.publish({
        goal: goal.trim(),
        limits: {
          max_iterations: maxIterations,
          token_budget: tokenBudget,
          target_score: targetScore,
        },
      });
      router.push(`/tasks/${task.id}`);
    } catch (err) {
      const message =
        err instanceof ApiError ? err.message : 'Could not reach the API. Is it running?';
      setError(message);
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      className="rounded-2xl border border-black/10 bg-white/60 p-5 shadow-sm dark:border-white/10 dark:bg-white/[0.03]"
    >
      <label htmlFor="goal" className="text-sm font-medium opacity-80">
        Publish a task
      </label>
      <textarea
        id="goal"
        value={goal}
        onChange={(e) => setGoal(e.target.value)}
        placeholder="Describe what you want produced. The agent will draft it, critique itself, and improve it pass by pass."
        rows={3}
        className="mt-2 w-full resize-y rounded-lg border border-black/10 bg-transparent px-3 py-2 text-sm outline-none focus:border-blue-500/60 dark:border-white/15"
      />

      <div className="mt-2 flex flex-wrap gap-2">
        {EXAMPLES.map((ex) => (
          <button
            key={ex}
            type="button"
            onClick={() => setGoal(ex)}
            className="rounded-full border border-black/10 px-2.5 py-1 text-xs opacity-70 hover:opacity-100 dark:border-white/15"
          >
            {ex.length > 42 ? `${ex.slice(0, 42)}…` : ex}
          </button>
        ))}
      </div>

      <div className="mt-5 grid gap-5 sm:grid-cols-3">
        <Slider
          label="Max passes"
          value={maxIterations}
          min={1}
          max={d.max_iterations_cap}
          step={1}
          onChange={setMaxIterations}
          display={(v) => `${v}`}
        />
        <Slider
          label="Token budget"
          value={tokenBudget}
          min={5000}
          max={d.token_budget_cap}
          step={5000}
          onChange={setTokenBudget}
          display={(v) => `${Math.round(v / 1000)}k`}
        />
        <Slider
          label="Target score"
          value={targetScore}
          min={50}
          max={100}
          step={1}
          onChange={setTargetScore}
          display={(v) => `${v}`}
        />
      </div>

      {error && <p className="mt-4 text-sm text-red-600 dark:text-red-400">{error}</p>}

      <div className="mt-5 flex items-center justify-between">
        <p className="text-xs opacity-50">
          The loop stops at the target, the cap, the budget, or when it stops improving.
        </p>
        <button
          type="submit"
          disabled={goal.trim().length < 4 || submitting}
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {submitting ? 'Starting…' : 'Run the loop'}
        </button>
      </div>
    </form>
  );
}

function Slider({
  label,
  value,
  min,
  max,
  step,
  onChange,
  display,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  display: (v: number) => string;
}) {
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between">
        <span className="text-xs font-medium opacity-70">{label}</span>
        <span className="text-xs font-semibold tabular-nums">{display(value)}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-blue-600"
      />
    </div>
  );
}
