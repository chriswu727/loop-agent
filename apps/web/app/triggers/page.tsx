'use client';

import type { Trigger } from '@repo/api-contract';
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
  const [requireApproval, setRequireApproval] = useState(false);
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
    load();
  }, [load]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (name.trim().length < 1 || goal.trim().length < 4 || busy) return;
    setBusy(true);
    setError(null);
    try {
      await triggersApi.create({
        name: name.trim(),
        goal: goal.trim(),
        allowed_tools: noShell ? ['write_file', 'edit_file', 'read_file'] : null,
        allow_egress: allowNetwork,
        require_approval: requireApproval,
      });
      setName('');
      setGoal('');
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create the trigger.');
    } finally {
      setBusy(false);
    }
  }

  async function fire(id: string) {
    try {
      const task = await triggersApi.fire(id);
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
            <input type="checkbox" checked={noShell} onChange={(e) => setNoShell(e.target.checked)} />
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
          <button
            type="submit"
            disabled={busy || name.trim().length < 1 || goal.trim().length < 4}
            className="ml-auto rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-blue-500 disabled:opacity-40"
          >
            Save trigger
          </button>
        </div>
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
                fired {t.fire_count}×{t.require_approval && ' · approval'}
                {t.allow_egress && ' · network'}
                {t.allowed_tools && ' · files-only'}
              </p>
            </div>
            <div className="flex shrink-0 gap-2 text-xs">
              <button
                onClick={() => fire(t.id)}
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
