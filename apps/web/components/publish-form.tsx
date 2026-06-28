'use client';

import type { LimitDefaults } from '@repo/api-contract';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { ApiError, tasksApi } from '@/lib/api-client';

const FALLBACK: LimitDefaults = {
  max_steps_default: 12,
  max_steps_cap: 40,
  token_budget_default: 60000,
  token_budget_cap: 200000,
};

const EXAMPLES = [
  'Write a Python script that prints the first 15 Fibonacci numbers, then run it to confirm the output.',
  'Attach a spreadsheet, then: add a Total column summing each row, and save it.',
  'Attach a .docx, then: fix typos and add a one-paragraph summary at the top.',
];

export function PublishForm({ defaults }: { defaults: LimitDefaults | null }) {
  const d = defaults ?? FALLBACK;
  const router = useRouter();

  const [goal, setGoal] = useState('');
  const [maxSteps, setMaxSteps] = useState(d.max_steps_default);
  const [tokenBudget, setTokenBudget] = useState(d.token_budget_default);
  const [files, setFiles] = useState<File[]>([]);
  const [noShell, setNoShell] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (goal.trim().length < 4 || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const limits = { max_steps: maxSteps, token_budget: tokenBudget };
      // Least-authority: when "no shell" is on, grant only the file tools.
      const allowed_tools = noShell ? ['write_file', 'edit_file', 'read_file'] : null;
      if (files.length > 0) {
        // Draft first so files land in the workspace, then start the agent.
        const task = await tasksApi.publish({
          goal: goal.trim(),
          limits,
          allowed_tools,
          autostart: false,
        });
        for (const file of files) await tasksApi.upload(task.id, file);
        await tasksApi.start(task.id);
        router.push(`/tasks/${task.id}`);
      } else {
        const task = await tasksApi.publish({ goal: goal.trim(), limits, allowed_tools });
        router.push(`/tasks/${task.id}`);
      }
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
        placeholder="Describe a goal. The agent will plan it, write files and run commands in its own workspace, check its own work, and keep going until it's done."
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

      <div className="mt-3 flex flex-wrap items-center gap-3 text-xs">
        <label className="cursor-pointer rounded-lg border border-black/10 px-3 py-1.5 opacity-80 hover:opacity-100 dark:border-white/15">
          Attach files
          <input
            type="file"
            multiple
            className="hidden"
            onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
          />
        </label>
        {files.map((f) => (
          <span key={f.name} className="rounded-full bg-blue-500/10 px-2 py-1 text-blue-600 dark:text-blue-400">
            {f.name}
          </span>
        ))}
        {files.length > 0 && (
          <button type="button" onClick={() => setFiles([])} className="opacity-50 hover:opacity-100">
            clear
          </button>
        )}
        <label className="ml-auto flex cursor-pointer items-center gap-1.5 opacity-80">
          <input type="checkbox" checked={noShell} onChange={(e) => setNoShell(e.target.checked)} />
          No shell (files only)
        </label>
      </div>

      <div className="mt-5 grid gap-5 sm:grid-cols-2">
        <Slider
          label="Max steps"
          value={maxSteps}
          min={1}
          max={d.max_steps_cap}
          step={1}
          onChange={setMaxSteps}
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
      </div>

      {error && <p className="mt-4 text-sm text-red-600 dark:text-red-400">{error}</p>}

      <div className="mt-5 flex items-center justify-between">
        <p className="text-xs opacity-50">
          The agent stops when the goal is verified, or it runs out of steps or budget.
        </p>
        <button
          type="submit"
          disabled={goal.trim().length < 4 || submitting}
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {submitting ? 'Starting…' : 'Run the agent'}
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
