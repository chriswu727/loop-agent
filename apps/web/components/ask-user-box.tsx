'use client';

import type { Task } from '@repo/api-contract';
import { useState } from 'react';
import { ApiError, tasksApi } from '@/lib/api-client';

/** Shown when the agent paused to ask the user something. Answering resumes it. */
export function AskUserBox({
  taskId,
  question,
  onAnswered,
}: {
  taskId: string;
  question: string;
  onAnswered: (task: Task) => void;
}) {
  const [answer, setAnswer] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!answer.trim() || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const task = await tasksApi.respond(taskId, answer.trim());
      onAnswered(task);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not send your answer.');
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      className="mt-6 rounded-2xl border border-purple-500/30 bg-purple-500/5 p-5"
    >
      <p className="text-xs font-semibold uppercase tracking-wide text-purple-600 dark:text-purple-400">
        The agent needs your input
      </p>
      <p className="mt-2 whitespace-pre-wrap text-sm">{question}</p>
      <div className="mt-3 flex gap-2">
        <input
          autoFocus
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
          placeholder="Type your answer…"
          className="flex-1 rounded-lg border border-black/10 bg-transparent px-3 py-2 text-sm outline-none focus:border-purple-500/60 dark:border-white/15"
        />
        <button
          type="submit"
          disabled={!answer.trim() || submitting}
          className="rounded-lg bg-purple-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-purple-500 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {submitting ? 'Sending…' : 'Answer'}
        </button>
      </div>
      {error && <p className="mt-2 text-sm text-red-600 dark:text-red-400">{error}</p>}
    </form>
  );
}
