'use client';

import { useEffect, useState } from 'react';
import { tasksApi } from '@/lib/api-client';

interface ReceiptCheck {
  kind: string;
  target: string;
  passed: boolean;
  evidence: string;
}
interface ReceiptFile {
  path: string;
  size: number;
  sha256: string;
}
interface Receipt {
  receipt_hash: string;
  goal: string;
  verified_by: string;
  isolation?: string;
  score: number;
  checks?: ReceiptCheck[];
  ledger_head?: string;
  files?: ReceiptFile[];
}

export function ReceiptPanel({ taskId }: { taskId: string }) {
  const [data, setData] = useState<{ receipt: Receipt; valid: boolean } | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    tasksApi
      .receipt(taskId)
      .then((d) => setData(d as unknown as { receipt: Receipt; valid: boolean }))
      .catch(() => {});
  }, [taskId]);

  if (!data) return null;
  const r = data.receipt;

  return (
    <section className="mt-6 rounded-xl border border-black/10 bg-white/50 dark:border-white/10 dark:bg-white/[0.02]">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left text-sm"
      >
        <span className="flex items-center gap-2 font-medium">
          Receipt
          <span
            className={
              data.valid
                ? 'rounded bg-green-500/15 px-1.5 py-0.5 text-xs font-normal text-green-600 dark:text-green-400'
                : 'rounded bg-red-500/15 px-1.5 py-0.5 text-xs font-normal text-red-600 dark:text-red-400'
            }
          >
            {data.valid ? 'hash verified' : 'hash mismatch'}
          </span>
        </span>
        <span className="text-xs opacity-50">{open ? 'hide' : 'show'}</span>
      </button>

      {open && (
        <div className="space-y-3 border-t border-black/10 px-4 py-3 text-xs dark:border-white/10">
          <p className="opacity-70">
            Verified by <b>{r.verified_by}</b>
            {r.isolation ? (
              <>
                {' · isolation '}
                <b>{r.isolation}</b>
              </>
            ) : null}
            {' · score '}
            <b>{r.score}/100</b>
          </p>

          {r.checks && r.checks.length > 0 && (
            <div>
              <p className="mb-1 opacity-50">Checks (re-run on a fresh copy of the workspace)</p>
              <ul className="space-y-1">
                {r.checks.map((c, i) => (
                  <li key={i} className="font-mono">
                    <span className={c.passed ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}>
                      [{c.passed ? 'PASS' : 'FAIL'}]
                    </span>{' '}
                    {c.kind} {c.target} — {c.evidence}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {r.files && r.files.length > 0 && (
            <div>
              <p className="mb-1 opacity-50">Output files (sha256)</p>
              <ul className="space-y-1 font-mono">
                {r.files.map((f) => (
                  <li key={f.path} className="truncate opacity-70">
                    {f.path} ({f.size}b) {f.sha256.slice(0, 16)}…
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="space-y-1 font-mono opacity-50">
            {r.ledger_head ? <p className="break-all">ledger head: {r.ledger_head}</p> : null}
            <p className="break-all">receipt hash: {r.receipt_hash}</p>
          </div>
          <p className="opacity-40">
            Verify independently: <code>make verify-receipt f=&lt;workspace&gt;/receipt.json</code>
          </p>
        </div>
      )}
    </section>
  );
}
