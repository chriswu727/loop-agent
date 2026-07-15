'use client';

import { useDesktopState } from '@/lib/desktop';

export function DesktopRuntimeBar() {
  const state = useDesktopState();
  if (!state) return null;

  const attention = state.runtime !== 'ready' || state.recoveryRequired;
  const restartable =
    state.runtime === 'failed' || state.runtime === 'stopped' || state.recoveryRequired;

  return (
    <aside
      className={`flex min-h-9 items-center justify-center gap-3 border-b px-4 py-2 text-xs ${
        attention
          ? 'border-amber-500/30 bg-amber-500/10 text-amber-800 dark:text-amber-300'
          : 'border-emerald-500/20 bg-emerald-500/5 text-emerald-800 dark:text-emerald-300'
      }`}
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${
          state.runtime === 'ready' ? 'bg-emerald-500' : 'bg-amber-500'
        }`}
      />
      <span>
        {state.runtime === 'ready'
          ? 'Desktop runtime verified'
          : `Desktop runtime: ${state.runtime}`}
        {state.project ? ` · ${state.project.name}` : ''}
        {state.recoveryRequired ? ' · previous session was interrupted' : ''}
      </span>
      {restartable && state.project && (
        <button
          type="button"
          onClick={() => void window.loopDesktop?.restartRuntime()}
          className="rounded-md border border-current/20 px-2 py-0.5 font-semibold hover:bg-black/5 dark:hover:bg-white/5"
        >
          Check and restart
        </button>
      )}
      <button
        type="button"
        onClick={() => void window.loopDesktop?.openSettings()}
        className="rounded-md border border-current/20 px-2 py-0.5 font-semibold hover:bg-black/5 dark:hover:bg-white/5"
      >
        Desktop settings
      </button>
    </aside>
  );
}
