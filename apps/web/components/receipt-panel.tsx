'use client';

import { useEffect, useState } from 'react';
import { tasksApi } from '@/lib/api-client';

interface ReceiptCheck {
  check_id?: string;
  criterion_ids?: string[];
  kind: string;
  target: string;
  passed: boolean;
  evidence: string;
  source?: string;
  baseline_passed?: boolean | null;
}
interface ReceiptCriterion {
  id: string;
  text: string;
}
interface ReceiptFile {
  path: string;
  size: number;
  sha256: string;
}
interface Receipt {
  schema?: string;
  receipt_hash: string;
  goal: string;
  verified_by: string;
  isolation?: string;
  score: number;
  checks?: ReceiptCheck[];
  baseline_checks?: ReceiptCheck[];
  criteria?: ReceiptCriterion[];
  contract?: {
    criteria_source?: string;
    verification_mode?: string;
  };
  coverage?: {
    execution_backed?: boolean;
    covered_criteria?: string[];
  };
  ledger_head?: string;
  files?: ReceiptFile[];
  authority?: { resolved?: string[]; egress_hosts?: string[] };
  provenance?: {
    revision?: string;
    sandbox?: { mode?: string; image?: string; image_digest?: string | null };
    model?: { provider?: string; model?: string } | null;
    verifier?: { provider?: string; model?: string } | null;
    executor_models?: Array<{ provider?: string; model?: string }>;
  };
}

function IntegrityRow({ ok, label }: { ok: boolean; label: string }) {
  return (
    <li className="flex items-center gap-1.5">
      <span
        className={`h-1.5 w-1.5 rounded-full ${ok ? 'bg-green-500' : 'bg-red-500'}`}
        aria-hidden
      />
      <span className={ok ? 'opacity-70' : 'font-medium text-red-600 dark:text-red-400'}>
        {label}
      </span>
    </li>
  );
}

interface ReceiptReport {
  receipt: Receipt;
  valid: boolean;
  signature?: string; // unsigned | valid | invalid | unverifiable
  anchor_ok?: boolean;
  files_ok?: boolean;
  file_mismatches?: { path: string; reason: string }[];
  authentic?: boolean;
  assurance?: 'authentic' | 'integrity' | 'invalid';
}

export function ReceiptPanel({ taskId }: { taskId: string }) {
  const [data, setData] = useState<ReceiptReport | null>(null);
  const [open, setOpen] = useState(false);
  const [replaying, setReplaying] = useState(false);
  const [replayResult, setReplayResult] = useState<string | null>(null);

  useEffect(() => {
    tasksApi
      .receipt(taskId)
      .then((d) => setData(d as unknown as ReceiptReport))
      .catch(() => {});
  }, [taskId]);

  if (!data) return null;
  const r = data.receipt;
  const checks = r.checks ?? [];
  const baselineChecks = r.baseline_checks ?? [];

  async function replay() {
    setReplaying(true);
    setReplayResult(null);
    try {
      const result = await tasksApi.replayReceipt(taskId);
      setReplayResult(result.passed ? 'Replay passed' : 'Replay failed');
    } catch (error) {
      setReplayResult(error instanceof Error ? error.message : 'Replay failed');
    } finally {
      setReplaying(false);
    }
  }

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
            {data.assurance === 'authentic'
              ? 'authentic'
              : data.valid
                ? 'integrity verified'
                : 'invalid'}
          </span>
        </span>
        <span className="text-xs opacity-50">{open ? 'hide' : 'show'}</span>
      </button>

      {open && (
        <div className="space-y-3 border-t border-black/10 px-4 py-3 text-xs dark:border-white/10">
          <p className="opacity-70">
            Schema <b>{r.schema ?? 'legacy'}</b>
            {' · '}
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

          {r.criteria && r.criteria.length > 0 && (
            <div>
              <p className="mb-1 opacity-50">
                Acceptance contract · {r.contract?.criteria_source ?? 'unknown'} ·{' '}
                {r.contract?.verification_mode ?? 'legacy'}
              </p>
              <ul className="space-y-1.5">
                {r.criteria.map((criterion) => {
                  const evidence = checks.filter((check) =>
                    check.criterion_ids?.includes(criterion.id),
                  );
                  const proven = evidence.length > 0 && evidence.every((check) => check.passed);
                  return (
                    <li
                      key={criterion.id}
                      className="rounded-lg border border-black/10 p-2 dark:border-white/10"
                    >
                      <div className="flex items-start gap-2">
                        <span
                          className={`mt-1 h-1.5 w-1.5 shrink-0 rounded-full ${proven ? 'bg-green-500' : 'bg-amber-500'}`}
                        />
                        <div>
                          <p>
                            <span className="font-mono opacity-45">{criterion.id}</span>{' '}
                            {criterion.text}
                          </p>
                          <p className="mt-0.5 font-mono text-[10px] opacity-45">
                            {evidence.length > 0
                              ? evidence
                                  .map(
                                    (check) =>
                                      `${check.check_id ?? check.kind} [${check.source ?? 'agent'}]`,
                                  )
                                  .join(' · ')
                              : 'No mapped execution evidence'}
                          </p>
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

          {/* Layered integrity: content hash, ed25519 signature, DB anchor, file re-hash. */}
          <ul className="space-y-0.5">
            <IntegrityRow ok={data.valid} label="content hash" />
            {data.signature && data.signature !== 'unsigned' && (
              <IntegrityRow ok={data.signature === 'valid'} label={`signature ${data.signature}`} />
            )}
            {data.signature === 'unsigned' && (
              <li className="opacity-40">signature: unsigned (tamper-evident, not tamper-proof)</li>
            )}
            {data.anchor_ok !== undefined && (
              <IntegrityRow ok={data.anchor_ok} label="matches independent DB anchor" />
            )}
            {data.files_ok !== undefined && (
              <IntegrityRow
                ok={data.files_ok}
                label={
                  data.files_ok
                    ? 'output files match manifest'
                    : `output files ALTERED: ${(data.file_mismatches ?? []).map((m) => m.path).join(', ')}`
                }
              />
            )}
          </ul>

          {checks.length > 0 && (
            <div>
              <p className="mb-1 opacity-50">Checks (re-run on a fresh copy of the workspace)</p>
              <ul className="space-y-1">
                {checks.map((c, i) => (
                  <li key={i} className="font-mono">
                    <span
                      className={
                        c.passed
                          ? 'text-green-600 dark:text-green-400'
                          : 'text-red-600 dark:text-red-400'
                      }
                    >
                      [{c.passed ? 'PASS' : 'FAIL'}]
                    </span>{' '}
                    {c.check_id ? `${c.check_id} ` : ''}[{c.source ?? 'agent'}] {c.kind} {c.target}{' '}
                    — {c.evidence}
                    {c.criterion_ids && c.criterion_ids.length > 0
                      ? ` (${c.criterion_ids.join(', ')})`
                      : ''}
                    {c.baseline_passed !== undefined && c.baseline_passed !== null
                      ? ` (baseline ${c.baseline_passed ? 'PASS' : 'FAIL'})`
                      : ''}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {baselineChecks.length > 0 && (
            <div>
              <p className="mb-1 opacity-50">Pre-change baseline</p>
              <ul className="space-y-1">
                {baselineChecks.map((check, index) => (
                  <li key={index} className="font-mono opacity-70">
                    [{check.passed ? 'PASS' : 'FAIL'}] {check.check_id ?? check.kind} [
                    {check.source ?? 'system'}] {check.target}
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
            {r.authority?.resolved ? (
              <p className="break-all">authority: {r.authority.resolved.join(', ')}</p>
            ) : null}
            {r.provenance?.sandbox ? (
              <p className="break-all">
                sandbox: {r.provenance.sandbox.mode} {r.provenance.sandbox.image ?? ''}{' '}
                {r.provenance.sandbox.image_digest ?? ''}
              </p>
            ) : null}
            {r.provenance?.executor_models && r.provenance.executor_models.length > 0 ? (
              <p className="break-all">
                executor:{' '}
                {r.provenance.executor_models
                  .map((model) => `${model.provider ?? '?'}:${model.model ?? '?'}`)
                  .join(' → ')}
              </p>
            ) : null}
            {r.provenance?.verifier ? (
              <p className="break-all">
                verifier: {r.provenance.verifier.provider ?? '?'}:
                {r.provenance.verifier.model ?? '?'}
              </p>
            ) : null}
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={replay}
              disabled={replaying}
              className="rounded-md bg-blue-600 px-2.5 py-1 text-white disabled:opacity-50"
            >
              {replaying ? 'Replaying…' : 'Replay checks'}
            </button>
            {replayResult ? <span className="opacity-70">{replayResult}</span> : null}
          </div>
          <p className="opacity-40">
            Verify independently: <code>loop receipt verify &lt;workspace&gt;/receipt.json</code>
          </p>
        </div>
      )}
    </section>
  );
}
