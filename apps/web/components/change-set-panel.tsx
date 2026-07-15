'use client';

import type { ChangeSet } from '@repo/api-contract';
import { useEffect, useState } from 'react';
import { ApiError, tasksApi } from '@/lib/api-client';

const STATE_LABEL: Record<ChangeSet['state'], string> = {
  pending: 'Ready for review',
  applied: 'Applied to source',
  discarded: 'Discarded',
  reverted: 'Undone',
};

export function ChangeSetPanel({ taskId, revision }: { taskId: string; revision: string }) {
  const [changeSet, setChangeSet] = useState<ChangeSet | null>(null);
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    tasksApi
      .changes(taskId)
      .then((value) => {
        if (!cancelled) {
          setChangeSet(value);
          setError(null);
        }
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(reason instanceof ApiError ? reason.message : 'Could not load the change set.');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [revision, taskId]);

  async function act(action: 'apply' | 'discard' | 'undo') {
    if (
      action === 'discard' &&
      !window.confirm('Discard this change set? It will remain auditable but can no longer apply.')
    ) {
      return;
    }
    setActing(true);
    setError(null);
    try {
      const updated =
        action === 'apply'
          ? await tasksApi.applyChanges(taskId)
          : action === 'discard'
            ? await tasksApi.discardChanges(taskId)
            : await tasksApi.undoChanges(taskId);
      setChangeSet(updated);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : 'The change-set action failed.');
    } finally {
      setActing(false);
    }
  }

  if (loading && !changeSet) {
    return <p className="mt-6 text-sm opacity-50">Loading isolated project changes…</p>;
  }
  if (!changeSet) {
    return <p className="mt-6 text-sm text-red-600 dark:text-red-400">{error}</p>;
  }

  return (
    <section className="mt-6 overflow-hidden rounded-2xl border border-black/10 bg-white/50 dark:border-white/10 dark:bg-white/[0.02]">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-black/10 p-4 dark:border-white/10">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold">Verified change set</h2>
            <span className="rounded-full bg-blue-500/10 px-2 py-0.5 text-[11px] font-medium text-blue-600 dark:text-blue-400">
              {STATE_LABEL[changeSet.state]}
            </span>
          </div>
          <p className="mt-1 text-xs opacity-55">
            {changeSet.project_path} · {changeSet.base_branch ?? 'detached'} @{' '}
            <span className="font-mono">{changeSet.base_commit.slice(0, 12)}</span>
          </p>
        </div>
        <div className="flex gap-2">
          {changeSet.can_discard && (
            <button
              type="button"
              disabled={acting}
              onClick={() => act('discard')}
              className="rounded-lg border border-black/15 px-3 py-1.5 text-xs transition hover:bg-black/5 disabled:opacity-40 dark:border-white/20 dark:hover:bg-white/10"
            >
              Discard
            </button>
          )}
          {changeSet.can_apply && (
            <button
              type="button"
              disabled={acting}
              onClick={() => act('apply')}
              className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-blue-500 disabled:opacity-40"
            >
              Apply verified patch
            </button>
          )}
          {changeSet.can_undo && (
            <button
              type="button"
              disabled={acting}
              onClick={() => act('undo')}
              className="rounded-lg border border-amber-500/40 px-3 py-1.5 text-xs text-amber-700 transition hover:bg-amber-500/10 disabled:opacity-40 dark:text-amber-300"
            >
              Undo apply
            </button>
          )}
        </div>
      </div>

      <div className="p-4">
        <div className="flex flex-wrap gap-2 text-xs">
          {changeSet.files.map((file) => (
            <span
              key={`${file.previous_path ?? ''}:${file.path}`}
              className="rounded-md border border-black/10 px-2 py-1 font-mono dark:border-white/10"
            >
              <span className="mr-1 font-semibold opacity-60">{file.status}</span>
              {file.previous_path ? `${file.previous_path} → ` : ''}
              {file.path}
              {file.additions !== null && file.deletions !== null && (
                <span className="ml-2">
                  <span className="text-green-600 dark:text-green-400">+{file.additions}</span>{' '}
                  <span className="text-red-600 dark:text-red-400">−{file.deletions}</span>
                </span>
              )}
            </span>
          ))}
        </div>
        {changeSet.files.length === 0 && <p className="text-xs opacity-50">No project changes.</p>}
        {changeSet.blocked_reason && !changeSet.can_undo && (
          <p className="mt-3 text-xs text-amber-700 dark:text-amber-300">
            {changeSet.blocked_reason}
          </p>
        )}
        {error && <p className="mt-3 text-xs text-red-600 dark:text-red-400">{error}</p>}
        {changeSet.diff && (
          <details className="mt-4">
            <summary className="cursor-pointer text-xs font-medium opacity-70">Review diff</summary>
            <pre className="mt-2 max-h-[32rem] overflow-auto whitespace-pre rounded-xl bg-black/[0.04] p-3 font-mono text-[11px] leading-relaxed dark:bg-black/30">
              {changeSet.diff}
            </pre>
          </details>
        )}
        <p className="mt-3 font-mono text-[10px] opacity-35">
          patch sha256 {changeSet.patch_sha256}
        </p>
      </div>
    </section>
  );
}
