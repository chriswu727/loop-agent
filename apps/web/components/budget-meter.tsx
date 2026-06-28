import { cn } from '@/lib/utils';

/**
 * A labelled usage bar showing how much of a hard limit has been consumed.
 * Fills amber past 80% so an approaching ceiling is visible at a glance.
 */
export function BudgetMeter({
  label,
  used,
  limit,
  format = (n) => n.toLocaleString(),
}: {
  label: string;
  used: number;
  limit: number;
  format?: (n: number) => string;
}) {
  const pct = limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  const near = pct >= 80;

  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between text-xs">
        <span className="font-medium opacity-70">{label}</span>
        <span className="tabular-nums opacity-60">
          {format(used)} / {format(limit)}
        </span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-black/10 dark:bg-white/10">
        <div
          className={cn(
            'h-full rounded-full transition-all duration-500',
            near ? 'bg-amber-500' : 'bg-blue-500',
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
