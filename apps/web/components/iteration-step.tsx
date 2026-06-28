import type { Iteration } from '@repo/api-contract';
import { cn } from '@/lib/utils';

function scoreColor(score: number): string {
  if (score >= 85) return 'text-green-600 dark:text-green-400';
  if (score >= 60) return 'text-blue-600 dark:text-blue-400';
  if (score >= 35) return 'text-amber-600 dark:text-amber-400';
  return 'text-red-600 dark:text-red-400';
}

/** One pass of the loop: its score, the critic's notes, and what it cost. */
export function IterationStep({ iteration, isBest }: { iteration: Iteration; isBest: boolean }) {
  return (
    <div className="rounded-xl border border-black/10 bg-white/40 p-4 dark:border-white/10 dark:bg-white/[0.02]">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold">Pass {iteration.number}</span>
          {isBest && (
            <span className="rounded-full bg-green-500/15 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-green-600 dark:text-green-400">
              Best so far
            </span>
          )}
        </div>
        <div className="flex items-center gap-3 text-xs">
          <span className="tabular-nums opacity-50">{iteration.tokens.toLocaleString()} tok</span>
          <span className={cn('text-lg font-bold tabular-nums', scoreColor(iteration.score))}>
            {iteration.score}
          </span>
        </div>
      </div>
      {iteration.critique && (
        <p className="mt-2 whitespace-pre-wrap text-xs leading-relaxed opacity-70">
          {iteration.critique}
        </p>
      )}
    </div>
  );
}
