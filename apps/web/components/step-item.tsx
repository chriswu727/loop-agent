import type { Step } from '@repo/api-contract';
import { cn } from '@/lib/utils';

const TOOL_LABELS: Record<string, string> = {
  write_file: 'write file',
  read_file: 'read file',
  run_command: 'run command',
  finish: 'finish',
  invalid: 'invalid action',
};

const STATUS_STYLES: Record<string, string> = {
  ok: 'bg-green-500/15 text-green-600 dark:text-green-400',
  error: 'bg-red-500/15 text-red-600 dark:text-red-400',
  blocked: 'bg-amber-500/15 text-amber-600 dark:text-amber-400',
};

/** One step of the agent loop: its reasoning, the tool it used, and the result. */
export function StepItem({ step }: { step: Step }) {
  const primaryArg =
    (step.tool_args.path as string) ||
    (step.tool_args.command as string) ||
    (step.tool_args.summary as string) ||
    '';

  return (
    <div className="rounded-xl border border-black/10 bg-white/40 p-4 dark:border-white/10 dark:bg-white/[0.02]">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold tabular-nums opacity-50">{step.number}</span>
          <span className="rounded-md bg-blue-500/10 px-2 py-0.5 font-mono text-xs text-blue-600 dark:text-blue-400">
            {TOOL_LABELS[step.tool] ?? step.tool}
          </span>
          <span
            className={cn(
              'rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide',
              STATUS_STYLES[step.status] ?? 'bg-zinc-500/15 text-zinc-500',
            )}
          >
            {step.status}
          </span>
        </div>
        <span className="tabular-nums text-[11px] opacity-40">{step.tokens.toLocaleString()} tok</span>
      </div>

      {step.thought && <p className="mt-2 text-xs leading-relaxed opacity-75">{step.thought}</p>}

      {primaryArg && (
        <pre className="mt-2 overflow-x-auto rounded-md bg-black/5 px-2 py-1 font-mono text-[11px] opacity-70 dark:bg-white/5">
          {primaryArg.length > 200 ? `${primaryArg.slice(0, 200)}…` : primaryArg}
        </pre>
      )}

      {step.observation && (
        <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-md border border-black/5 bg-black/[0.03] px-2 py-1.5 font-mono text-[11px] leading-relaxed opacity-70 dark:border-white/5 dark:bg-black/20">
          {step.observation}
        </pre>
      )}
    </div>
  );
}
