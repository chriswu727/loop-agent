import type { Task } from '@repo/api-contract';

const LABELS: Record<Task['loop']['state'], string> = {
  queued: 'Queued',
  preparing: 'Preparing runtime',
  understanding: 'Understanding goal',
  planning: 'Planning next action',
  acting: 'Executing action',
  verifying: 'Verifying evidence',
  awaiting_input: 'Waiting for input',
  awaiting_approval: 'Waiting for approval',
  completed: 'Completed',
  stopped: 'Stopped',
  cancelled: 'Cancelled',
  failed: 'Failed',
};

function reasonLabel(reason: string) {
  const words = reason.replace(/[_:]+/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function LoopState({ loop }: { loop: Task['loop'] }) {
  return (
    <p className="mt-2 text-xs opacity-60" data-testid="loop-state">
      Loop phase: <span className="font-medium opacity-90">{LABELS[loop.state]}</span>
      <span> · transition #{loop.sequence}</span>
      {loop.transition_reason && (
        <span title={`Transition reason: ${loop.transition_reason}`}>
          {' '}
          · {reasonLabel(loop.transition_reason)}
        </span>
      )}
    </p>
  );
}
