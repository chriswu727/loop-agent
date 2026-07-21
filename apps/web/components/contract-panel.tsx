import type { Task } from '@repo/api-contract';

export function ContractPanel({ task }: { task: Task }) {
  const draft = task.contract;
  const criteria = draft?.criteria ?? task.rubric;
  if (criteria.length === 0 && !draft) return null;

  const source =
    task.criteria_source === 'user'
      ? 'user confirmed'
      : task.criteria_source === 'compiled'
        ? 'Loop compiled'
        : 'model generated';
  const baselineFailures = task.baseline_checks.filter((check) => check.passed === false).length;

  return (
    <section className="mt-6 rounded-2xl border border-black/10 bg-white/40 p-5 dark:border-white/10 dark:bg-white/[0.02]">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide opacity-50">
          Acceptance contract
        </h2>
        <span className="rounded bg-blue-500/10 px-1.5 py-0.5 text-[10px] text-blue-700 dark:text-blue-300">
          {source}
        </span>
        <span className="rounded bg-black/5 px-1.5 py-0.5 text-[10px] opacity-60 dark:bg-white/10">
          {task.verification_mode}
        </span>
        {task.contract_status === 'locked' && task.contract_hash && (
          <span className="font-mono text-[10px] text-green-700 dark:text-green-400">
            locked {task.contract_hash.slice(0, 12)}
          </span>
        )}
        {task.contract_status === 'awaiting_input' && (
          <span className="text-[10px] font-medium text-amber-700 dark:text-amber-400">
            needs clarification
          </span>
        )}
      </div>

      <ol className="mt-3 grid gap-2">
        {criteria.map((criterion, index) => (
          <li key={`${index}-${criterion}`} className="flex gap-2 text-xs leading-relaxed">
            <span className="font-mono opacity-40">{String(index + 1).padStart(3, '0')}</span>
            <span>{criterion}</span>
          </li>
        ))}
      </ol>

      {draft && (
        <>
          <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] opacity-55">
            <span>risk {draft.risk}</span>
            <span>confidence {draft.confidence}%</span>
            <span>{draft.checks.length} locked checks</span>
            <span>{draft.discovery.files_scanned} files discovered</span>
            <span>
              compiled by {draft.compiler.provider}/{draft.compiler.model}
            </span>
            <span>
              criticized by {draft.critique.provider}/{draft.critique.model}
            </span>
          </div>
          {!draft.critique.accepted && draft.critique.issues.length > 0 && (
            <ul className="mt-3 grid gap-1 rounded-lg bg-amber-500/10 p-3 text-xs text-amber-800 dark:text-amber-300">
              {draft.critique.issues.map((issue) => (
                <li key={issue}>• {issue}</li>
              ))}
            </ul>
          )}
          <details className="mt-3 text-xs">
            <summary className="cursor-pointer opacity-60 hover:opacity-100">
              Contract evidence and assumptions
            </summary>
            <div className="mt-2 grid gap-3 rounded-lg border border-black/10 p-3 dark:border-white/10">
              <p className="opacity-65">
                Manifests: {draft.discovery.manifests.join(', ') || 'none'} · Tests:{' '}
                {draft.discovery.test_files.length} · Existing build outputs:{' '}
                {draft.discovery.build_outputs.join(', ') || 'none'}
              </p>
              {draft.assumptions.length > 0 && (
                <ul className="grid gap-1 opacity-65">
                  {draft.assumptions.map((assumption) => (
                    <li key={assumption}>Assumption: {assumption}</li>
                  ))}
                </ul>
              )}
              <ul className="grid gap-1 font-mono text-[11px] opacity-65">
                {draft.checks.map((check) => (
                  <li key={check.id}>
                    {check.id} [{check.source}] {check.command ?? check.path}
                  </li>
                ))}
              </ul>
            </div>
          </details>
        </>
      )}

      {task.required_checks.length > 0 && (
        <p className="mt-3 text-[11px] opacity-50">
          {task.required_checks.length} required verification gate
          {task.required_checks.length === 1 ? '' : 's'} · baseline{' '}
          {task.baseline_checks.length > 0
            ? `${baselineFailures} pre-existing failure${baselineFailures === 1 ? '' : 's'}`
            : 'pending'}
        </p>
      )}
    </section>
  );
}
