import type { Task } from '@repo/api-contract';
import Link from 'next/link';
import { StatusPill } from './status-pill';

export function TaskCard({ task }: { task: Task }) {
  return (
    <Link
      href={`/tasks/${task.id}`}
      className="block rounded-xl border border-black/10 bg-white/40 p-4 transition hover:border-blue-500/40 hover:bg-white/70 dark:border-white/10 dark:bg-white/[0.02] dark:hover:bg-white/[0.05]"
    >
      <div className="flex items-start justify-between gap-3">
        <p className="line-clamp-2 text-sm font-medium">{task.goal}</p>
        <StatusPill status={task.status} />
      </div>
      <div className="mt-3 flex items-center gap-4 text-xs opacity-60">
        <span className="tabular-nums">
          Score <span className="font-semibold opacity-100">{task.best_score}</span>/
          {task.limits.target_score}
        </span>
        <span className="tabular-nums">
          {task.iterations_used}/{task.limits.max_iterations} passes
        </span>
        <span className="tabular-nums">{task.tokens_used.toLocaleString()} tok</span>
      </div>
    </Link>
  );
}
