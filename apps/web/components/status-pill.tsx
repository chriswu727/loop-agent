import type { TaskStatus } from '@repo/api-contract';
import { cn } from '@/lib/utils';

const STYLES: Record<TaskStatus, string> = {
  pending: 'bg-zinc-500/15 text-zinc-500',
  running: 'bg-blue-500/15 text-blue-600 dark:text-blue-400',
  awaiting_input: 'bg-purple-500/15 text-purple-600 dark:text-purple-400',
  completed: 'bg-green-500/15 text-green-600 dark:text-green-400',
  cancelled: 'bg-amber-500/15 text-amber-600 dark:text-amber-400',
  failed: 'bg-red-500/15 text-red-600 dark:text-red-400',
};

const LABELS: Record<TaskStatus, string> = {
  pending: 'Queued',
  running: 'Running',
  awaiting_input: 'Needs you',
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
  goal_achieved: 'Goal achieved (verified)',
  max_steps: 'Reached the step limit',
  budget_exhausted: 'Spent the token budget',
  stuck: 'Got stuck',
  cancelled: 'Cancelled by you',
  error: 'Errored out',
};

export function stopReasonLabel(reason: string | null): string | null {
  if (!reason) return null;
  return REASON_LABELS[reason] ?? reason;
}
