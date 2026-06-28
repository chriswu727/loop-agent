import type { TaskStatus } from '@repo/api-contract';
import { cn } from '@/lib/utils';

const STYLES: Record<TaskStatus, string> = {
  pending: 'bg-zinc-500/15 text-zinc-500',
  running: 'bg-blue-500/15 text-blue-600 dark:text-blue-400',
  completed: 'bg-green-500/15 text-green-600 dark:text-green-400',
  cancelled: 'bg-amber-500/15 text-amber-600 dark:text-amber-400',
  failed: 'bg-red-500/15 text-red-600 dark:text-red-400',
};

const LABELS: Record<TaskStatus, string> = {
  pending: 'Queued',
  running: 'Running',
  completed: 'Done',
  cancelled: 'Cancelled',
  failed: 'Failed',
};

export function StatusPill({ status }: { status: TaskStatus }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium',
        STYLES[status],
      )}
    >
      <span
        className={cn(
          'h-1.5 w-1.5 rounded-full bg-current',
          status === 'running' && 'animate-pulse',
        )}
      />
      {LABELS[status]}
    </span>
  );
}

const REASON_LABELS: Record<string, string> = {
  target_reached: 'Hit the target score',
  max_iterations: 'Reached the iteration cap',
  budget_exhausted: 'Spent the token budget',
  plateau: 'Stopped improving',
  cancelled: 'Cancelled by you',
  error: 'Errored out',
};

export function stopReasonLabel(reason: string | null): string | null {
  if (!reason) return null;
  return REASON_LABELS[reason] ?? reason;
}
