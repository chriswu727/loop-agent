'use client';

import type { Capability, Trigger } from '@repo/api-contract';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';
import { ApiError, triggersApi } from '@/lib/api-client';

export default function TriggersPage() {
  const router = useRouter();
  const [triggers, setTriggers] = useState<Trigger[]>([]);
  const [name, setName] = useState('');
  const [goal, setGoal] = useState('');
  const [noShell, setNoShell] = useState(false);
  const [allowNetwork, setAllowNetwork] = useState(false);
  const [egressHosts, setEgressHosts] = useState('');
  const [requireApproval, setRequireApproval] = useState(false);
  const [intervalMin, setIntervalMin] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setTriggers(await triggersApi.list());
    } catch {
      /* API may be down; leave the list empty */
    }
  }, []);

  useEffect(() => {
    let active = true;
    triggersApi
      .list()
      .then((t) => {
        if (active) setTriggers(t);
      })
      .catch(() => {
        /* API may be down; leave the list empty */
      });
    return () => {
      active = false;
    };
  }, []);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (name.trim().length < 1 || goal.trim().length < 4 || busy) return;
    if (allowNetwork && !egressHosts.trim()) {
      setError('Network triggers require at least one destination host.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const interval = parseInt(intervalMin, 10);
      const capabilities: Capability[] = [
        'fs.read',
        'fs.write',
        'memory.read',
        'memory.write',
        'task.spawn',
      ];
      if (!noShell) capabilities.push('exec');
      if (allowNetwork) capabilities.push('net.shell');
      await triggersApi.create({
        name: name.trim(),
        goal: goal.trim(),
        capabilities,
        allow_egress: allowNetwork,
        egress_hosts: allowNetwork
          ? egressHosts
              .split(',')
              .map((host) => host.trim())
              .filter(Boolean)
          : null,
        require_approval: requireApproval,
        interval_minutes: Number.isFinite(interval) && interval >= 1 ? interval : null,
      });
      setName('');
      setGoal('');
      setIntervalMin('');
      setEgressHosts('');
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create the trigger.');
    } finally {
      setBusy(false);
    }
  }

  async function fire(t: Trigger) {
    try {
      const task = await triggersApi.fire(t.id, t.secret);
      router.push(`/tasks/${task.id}`);
    } catch {
      /* a disabled or missing trigger; reload reconciles */
      await load();
    }
  }

  async function remove(id: string) {
    await triggersApi.remove(id).catch(() => undefined);
    await load();
  }

  return (
    <main className="mx-auto max-w-3xl px-6 py-14">
      <Link href="/" className="text-sm opacity-60 transition hover:opacity-100">
        ← Home
      </Link>
      <h1 className="mt-4 text-2xl font-bold tracking-tight">Triggers</h1>
      <p className="mt-1 text-sm opacity-60">
        Saved task templates. Fire one from here, or hit its endpoint from any external event — the
        agent runs the task with the same safety settings you set here.
      </p>

      <form
        onSubmit={create}
        className="mt-6 rounded-2xl border border-black/10 bg-white/60 p-5 dark:border-white/10 dark:bg-white/[0.03]"
      >
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Trigger name"
          className="w-full rounded-lg border border-black/10 bg-transparent px-3 py-2 text-sm outline-none focus:border-blue-500/60 dark:border-white/15"
        />
        <textarea
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          placeholder="What should the agent do each time this fires?"
          rows={2}
          className="mt-2 w-full resize-y rounded-lg border border-black/10 bg-transparent px-3 py-2 text-sm outline-none focus:border-blue-500/60 dark:border-white/15"
        />
        <div className="mt-3 flex flex-wrap items-center gap-4 text-xs">
          <label className="flex cursor-pointer items-center gap-1.5 opacity-80">
            <input
              type="checkbox"
              checked={noShell}
              onChange={(e) => setNoShell(e.target.checked)}
            />
            No shell
          </label>
          <label className="flex cursor-pointer items-center gap-1.5 opacity-80">
            <input
              type="checkbox"
              checked={allowNetwork}
              onChange={(e) => setAllowNetwork(e.target.checked)}
            />
            Allow network
          </label>
          <label className="flex cursor-pointer items-center gap-1.5 opacity-80">
            <input
              type="checkbox"
              checked={requireApproval}
              onChange={(e) => setRequireApproval(e.target.checked)}
            />
            Require approval
          </label>
          <label className="flex items-center gap-1.5 opacity-80">
            every
            <input
              type="number"
              min={1}
              value={intervalMin}
              onChange={(e) => setIntervalMin(e.target.value)}
              placeholder="—"
              className="w-14 rounded-md border border-black/10 bg-transparent px-2 py-1 dark:border-white/15"
            />
            min
          </label>
          <button
            type="submit"
            disabled={
              busy ||
              name.trim().length < 1 ||
              goal.trim().length < 4 ||
              (allowNetwork && !egressHosts.trim())
            }
            className="ml-auto rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-blue-500 disabled:opacity-40"
          >
            Save trigger
          </button>
        </div>
        {allowNetwork && (
          <input
            required
            aria-label="Allowed destination hosts"
            value={egressHosts}
            onChange={(e) => setEgressHosts(e.target.value)}
            placeholder="Required destinations (comma-separated)"
            className="mt-3 w-full rounded-lg border border-black/10 bg-transparent px-3 py-1.5 text-xs outline-none focus:border-blue-500/60 dark:border-white/15"
          />
        )}
        {error && <p className="mt-2 text-sm text-red-600 dark:text-red-400">{error}</p>}
      </form>

      <section className="mt-8 grid gap-3">
        {triggers.length === 0 && (
          <p className="text-sm opacity-50">No triggers yet. Save one above.</p>
        )}
        {triggers.map((t) => (
          <div
            key={t.id}
            className="flex items-start justify-between gap-3 rounded-xl border border-black/10 bg-white/40 p-4 dark:border-white/10 dark:bg-white/[0.02]"
          >
            <div className="min-w-0">
              <p className="text-sm font-medium">{t.name}</p>
              <p className="line-clamp-2 text-xs opacity-60">{t.goal}</p>
              <p className="mt-1 text-[11px] opacity-40">
                fired {t.fire_count}×{t.interval_minutes && ` · every ${t.interval_minutes}m`}
                {t.require_approval && ' · approval'}
                {t.allow_egress && ' · network'}
                {t.allowed_tools && ' · files-only'}
              </p>
              <p className="mt-1 truncate font-mono text-[10px] opacity-30" title="Webhook URL">
                POST /hooks/triggers/{t.id} · X-Trigger-Secret: {t.secret}
              </p>
            </div>
            <div className="flex shrink-0 gap-2 text-xs">
              <button
                onClick={() => fire(t)}
                className="rounded-md bg-blue-600 px-3 py-1.5 font-medium text-white hover:bg-blue-500"
              >
                Fire
              </button>
              <button
                onClick={() => remove(t.id)}
                className="rounded-md border border-black/10 px-3 py-1.5 opacity-60 hover:opacity-100 dark:border-white/15"
              >
                Delete
              </button>
            </div>
          </div>
        ))}
      </section>
    </main>
  );
}
