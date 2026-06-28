'use client';

import type { Step, Task } from '@repo/api-contract';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';
import { BudgetMeter } from '@/components/budget-meter';
import { StepItem } from '@/components/step-item';
import { StatusPill, stopReasonLabel } from '@/components/status-pill';
import { ApiError, tasksApi } from '@/lib/api-client';

const TERMINAL = new Set(['completed', 'cancelled', 'failed']);
const POLL_MS = 1200;

export default function TaskDetail() {
  const params = useParams<{ id: string }>();
  const id = params.id;

  const [task, setTask] = useState<Task | null>(null);
  const [steps, setSteps] = useState<Step[]>([]);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const poll = useCallback(async () => {
    try {
      const [t, s] = await Promise.all([tasksApi.get(id), tasksApi.steps(id)]);
      setTask(t);
      setSteps(s);
      setError(null);
      if (!TERMINAL.has(t.status)) {
        timer.current = setTimeout(poll, POLL_MS);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Lost contact with the API.');
      timer.current = setTimeout(poll, POLL_MS * 2);
    }
  }, [id]);

  useEffect(() => {
    poll();
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [poll]);

  async function cancel() {
    try {
      const t = await tasksApi.cancel(id);
      setTask(t);
    } catch {
      /* a finished task can't be cancelled; the next poll reconciles state */
    }
  }

  if (!task) {
    return (
      <main className="mx-auto max-w-3xl px-6 py-14">
        <BackLink />
        <p className="mt-8 text-sm opacity-50">{error ?? 'Loading task…'}</p>
      </main>
    );
  }

  const active = !TERMINAL.has(task.status);
  const reason = stopReasonLabel(task.stop_reason);
  const achieved = task.stop_reason === 'goal_achieved';

  return (
    <main className="mx-auto max-w-3xl px-6 py-14">
      <BackLink />

      <div className="mt-4 flex items-start justify-between gap-4">
        <h1 className="text-lg font-semibold leading-snug">{task.goal}</h1>
        <StatusPill status={task.status} />
      </div>

      {reason && task.status === 'completed' && (
        <p className="mt-2 text-xs opacity-60">
          Stopped: {reason}
          {achieved && ` · verified ${task.verification_score}/100`}.
        </p>
      )}
      {task.status === 'failed' && task.error && (
        <p className="mt-2 text-xs text-red-600 dark:text-red-400">Error: {task.error}</p>
      )}

      {/* Live progress: the hard limits being consumed. */}
      <section className="mt-6 grid gap-4 rounded-2xl border border-black/10 bg-white/40 p-5 dark:border-white/10 dark:bg-white/[0.02] sm:grid-cols-2">
        <BudgetMeter label="Steps" used={task.steps_used} limit={task.limits.max_steps} />
        <BudgetMeter label="Token budget" used={task.tokens_used} limit={task.limits.token_budget} />
      </section>

      {task.rubric.length > 0 && (
        <section className="mt-6">
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide opacity-50">
            Success criteria
          </h2>
          <ul className="flex flex-wrap gap-2">
            {task.rubric.map((c, i) => (
              <li
                key={i}
                className="rounded-full border border-black/10 px-2.5 py-1 text-xs opacity-70 dark:border-white/15"
              >
                {c}
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* The agent's final account of what it did. */}
      {task.summary && (
        <section className="mt-6">
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide opacity-50">Result</h2>
          <p className="whitespace-pre-wrap rounded-xl border border-black/10 bg-white/60 p-4 text-sm leading-relaxed dark:border-white/10 dark:bg-black/30">
            {task.summary}
          </p>
        </section>
      )}

      {task.workspace_path && (
        <p className="mt-3 break-all font-mono text-[11px] opacity-40">
          workspace: {task.workspace_path}
        </p>
      )}

      {active && (
        <button
          onClick={cancel}
          className="mt-4 rounded-lg border border-red-500/40 px-3 py-1.5 text-sm text-red-600 transition hover:bg-red-500/10 dark:text-red-400"
        >
          Cancel run
        </button>
      )}

      {/* The agent's step-by-step trace, newest first. */}
      <section className="mt-8">
        <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide opacity-50">
          Steps ({steps.length})
          {active && <span className="ml-1 opacity-60">· working…</span>}
        </h2>
        <div className="grid gap-3">
          {[...steps].reverse().map((step) => (
            <StepItem key={step.id} step={step} />
          ))}
          {steps.length === 0 && (
            <p className="text-sm opacity-50">Planning the first step…</p>
          )}
        </div>
      </section>
    </main>
  );
}

function BackLink() {
  return (
    <Link href="/" className="text-sm opacity-60 transition hover:opacity-100">
      ← All tasks
    </Link>
  );
}
