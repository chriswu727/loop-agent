'use client';

import type { Task } from '@repo/api-contract';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { FormEvent, useEffect, useState } from 'react';
import { ApiError, tasksApi } from '@/lib/api-client';

export function ProductSessionPanel({ task }: { task: Task }) {
  const router = useRouter();
  const [revisions, setRevisions] = useState<Task[]>([]);
  const [feedback, setFeedback] = useState('');
  const [kind, setKind] = useState<'implementation_fix' | 'product_decision'>('implementation_fix');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const revisionNumber = task.product_revision?.revision;

  useEffect(() => {
    if (!revisionNumber) return;
    tasksApi
      .revisions(task.id)
      .then(setRevisions)
      .catch((reason: unknown) =>
        setError(reason instanceof ApiError ? reason.message : 'Could not load product revisions.'),
      );
  }, [task.id, revisionNumber]);

  if (!task.product_revision) return null;

  const revision = task.product_revision;
  const canRevise =
    revision.is_latest &&
    task.status === 'completed' &&
    task.stop_reason === 'goal_achieved' &&
    task.verified_by === 'execution' &&
    (task.change_set?.state === 'pending' || task.change_set?.state === 'reverted');

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (feedback.trim().length < 4) return;
    setSubmitting(true);
    setError(null);
    try {
      const next = await tasksApi.createRevision(task.id, {
        feedback: feedback.trim(),
        kind,
      });
      router.push(`/tasks/${next.id}`);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : 'Could not create the next revision.');
      setSubmitting(false);
    }
  }

  return (
    <section className="mt-6 rounded-2xl border border-violet-500/20 bg-violet-500/[0.04] p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold">Product Session</h2>
          <p className="mt-1 text-xs opacity-55">
            Versioned specifications, feedback deltas, Receipts, and change sets stay linked.
          </p>
        </div>
        <span className="rounded-full bg-violet-500/15 px-2.5 py-1 text-xs font-medium text-violet-700 dark:text-violet-300">
          v{revision.revision}
        </span>
      </div>

      {revisions.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-2" aria-label="Product revisions">
          {revisions.map((item) => {
            const itemRevision = item.product_revision;
            if (!itemRevision) return null;
            const current = item.id === task.id;
            return (
              <Link
                key={item.id}
                href={`/tasks/${item.id}`}
                className={`rounded-lg border px-2.5 py-1.5 text-xs transition ${
                  current
                    ? 'border-violet-500/50 bg-violet-500/15'
                    : 'border-black/10 hover:border-violet-500/40 dark:border-white/10'
                }`}
              >
                v{itemRevision.revision} · {item.receipt_hash ? 'Receipt' : item.status}
              </Link>
            );
          })}
        </div>
      )}

      {revision.feedback_delta && (
        <div className="mt-4 rounded-xl border border-black/10 bg-white/50 p-3 text-xs dark:border-white/10 dark:bg-black/20">
          <span className="font-medium">
            {revision.feedback_kind === 'implementation_fix'
              ? 'Implementation correction'
              : 'Product decision'}
          </span>
          <p className="mt-1 whitespace-pre-wrap opacity-70">{revision.feedback_delta}</p>
        </div>
      )}

      <p className="mt-3 break-all font-mono text-[10px] opacity-35">
        specification {revision.specification_hash}
      </p>

      {canRevise ? (
        <form className="mt-5 border-t border-black/10 pt-4 dark:border-white/10" onSubmit={submit}>
          <label className="text-xs font-medium" htmlFor="product-feedback">
            Continue from this verified delivery
          </label>
          <select
            aria-label="Feedback type"
            className="mt-2 w-full rounded-lg border border-black/10 bg-transparent px-3 py-2 text-sm dark:border-white/15"
            value={kind}
            onChange={(event) =>
              setKind(event.target.value as 'implementation_fix' | 'product_decision')
            }
          >
            <option value="implementation_fix">Bug or implementation correction</option>
            <option value="product_decision">Changed product decision</option>
          </select>
          <textarea
            id="product-feedback"
            className="mt-2 min-h-24 w-full resize-y rounded-lg border border-black/10 bg-transparent px-3 py-2 text-sm outline-none focus:border-violet-500/60 dark:border-white/15"
            value={feedback}
            onChange={(event) => setFeedback(event.target.value)}
            maxLength={4000}
            placeholder="Describe what is wrong or what should change. Loop will preserve this as the next revision's contract input."
          />
          <button
            type="submit"
            disabled={submitting || feedback.trim().length < 4}
            className="mt-2 rounded-lg bg-violet-600 px-3 py-2 text-sm font-medium text-white transition hover:bg-violet-500 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {submitting ? 'Creating v' + (revision.revision + 1) + '…' : 'Create next revision'}
          </button>
        </form>
      ) : revision.is_latest ? (
        <p className="mt-4 text-xs opacity-55">
          The next revision unlocks after execution-verified delivery and before Apply. Undo an
          applied change set first if you want to continue this lineage.
        </p>
      ) : (
        <p className="mt-4 text-xs opacity-55">
          This is immutable history. Continue from the latest revision.
        </p>
      )}

      {error && <p className="mt-3 text-xs text-red-600 dark:text-red-400">{error}</p>}
    </section>
  );
}
