'use client';

import type { Iteration, Task } from '@repo/api-contract';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';
import { BudgetMeter } from '@/components/budget-meter';
import { IterationStep } from '@/components/iteration-step';
import { ScoreTrend } from '@/components/score-trend';
import { StatusPill, stopReasonLabel } from '@/components/status-pill';
import { ApiError, tasksApi } from '@/lib/api-client';

const TERMINAL = new Set(['completed', 'cancelled', 'failed']);
const POLL_MS = 1200;

export default function TaskDetail() {
  const params = useParams<{ id: string }>();
  const id = params.id;

  const [task, setTask] = useState<Task | null>(null);
  const [iterations, setIterations] = useState<Iteration[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const poll = useCallback(async () => {
    try {
      const [t, its] = await Promise.all([tasksApi.get(id), tasksApi.iterations(id)]);
      setTask(t);
      setIterations(its);
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

  async function copyArtifact() {
    if (!task?.best_artifact) return;
    await navigator.clipboard.writeText(task.best_artifact);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  if (!task) {
    return (
      <main className="mx-auto max-w-3xl px-6 py-14">
        <BackLink />
        <p className="mt-8 text-sm opacity-50">{error ?? 'Loading task…'}</p>
      </main>
    );
  }

  const bestIndex = iterations.reduce(
    (best, it, i) => (it.score > (iterations[best]?.score ?? -1) ? i : best),
    0,
  );
  const active = !TERMINAL.has(task.status);
  const reason = stopReasonLabel(task.stop_reason);

  return (
    <main className="mx-auto max-w-3xl px-6 py-14">
      <BackLink />

      <div className="mt-4 flex items-start justify-between gap-4">
        <h1 className="text-lg font-semibold leading-snug">{task.goal}</h1>
        <StatusPill status={task.status} />
      </div>

      {reason && task.status === 'completed' && (
        <p className="mt-2 text-xs opacity-60">Stopped: {reason}.</p>
      )}
      {task.status === 'failed' && task.error && (
        <p className="mt-2 text-xs text-red-600 dark:text-red-400">Error: {task.error}</p>
      )}

      {/* Live progress: score + the hard limits being consumed. */}
      <section className="mt-6 grid gap-5 rounded-2xl border border-black/10 bg-white/40 p-5 dark:border-white/10 dark:bg-white/[0.02] sm:grid-cols-2">
        <div>
          <div className="mb-1 flex items-baseline justify-between">
            <span className="text-xs font-medium opacity-70">Best score</span>
            <span className="text-sm font-bold tabular-nums">
              {task.best_score}
              <span className="opacity-40">/{task.limits.target_score}</span>
            </span>
          </div>
          <ScoreTrend
            scores={iterations.map((i) => i.score)}
            target={task.limits.target_score}
          />
        </div>
        <div className="flex flex-col justify-center gap-4">
          <BudgetMeter
            label="Passes"
            used={task.iterations_used}
            limit={task.limits.max_iterations}
          />
          <BudgetMeter label="Token budget" used={task.tokens_used} limit={task.limits.token_budget} />
        </div>
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

      {/* The current best artifact — the actual deliverable. */}
      <section className="mt-6">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-xs font-semibold uppercase tracking-wide opacity-50">
            Best result {active && <span className="opacity-60">(improving…)</span>}
          </h2>
          {task.best_artifact && (
            <button
              onClick={copyArtifact}
              className="rounded-md border border-black/10 px-2 py-1 text-xs opacity-70 hover:opacity-100 dark:border-white/15"
            >
              {copied ? 'Copied' : 'Copy'}
            </button>
          )}
        </div>
        <pre className="max-h-[28rem] overflow-auto whitespace-pre-wrap rounded-xl border border-black/10 bg-white/60 p-4 font-mono text-xs leading-relaxed dark:border-white/10 dark:bg-black/30">
          {task.best_artifact ?? (active ? 'Working on the first draft…' : 'No result produced.')}
        </pre>
      </section>

      {active && (
        <button
          onClick={cancel}
          className="mt-4 rounded-lg border border-red-500/40 px-3 py-1.5 text-sm text-red-600 transition hover:bg-red-500/10 dark:text-red-400"
        >
          Cancel run
        </button>
      )}

      {/* Iteration history, newest first. */}
      <section className="mt-8">
        <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide opacity-50">
          Iterations ({iterations.length})
        </h2>
        <div className="grid gap-3">
          {[...iterations].reverse().map((it) => (
            <IterationStep
              key={it.id}
              iteration={it}
              isBest={iterations[bestIndex]?.id === it.id}
            />
          ))}
          {iterations.length === 0 && (
            <p className="text-sm opacity-50">Waiting for the first pass…</p>
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
