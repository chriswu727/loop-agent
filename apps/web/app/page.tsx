import type { LimitDefaults, SkillInfo, Task } from '@repo/api-contract';
import { PublishForm } from '@/components/publish-form';
import { TaskCard } from '@/components/task-card';
import { apiBaseUrl, env } from '@/lib/env';

// Server component: load the task list + limit defaults at render time. The API
// may be down on a fresh checkout, so every fetch degrades to an empty state
// rather than throwing.
async function getData(): Promise<{
  tasks: Task[];
  defaults: LimitDefaults | null;
  memory: string;
  skills: SkillInfo[];
  up: boolean;
}> {
  const base = apiBaseUrl();
  const opts = { cache: 'no-store' as const, signal: AbortSignal.timeout(2500) };
  try {
    const [tasksRes, limitsRes, memRes, skillsRes] = await Promise.all([
      fetch(`${base}/api/v1/tasks?limit=50`, opts),
      fetch(`${base}/api/v1/tasks/limits`, opts),
      fetch(`${base}/api/v1/memory`, opts).catch(() => null),
      fetch(`${base}/api/v1/skills`, opts).catch(() => null),
    ]);
    const tasks = tasksRes.ok ? ((await tasksRes.json()).items as Task[]) : [];
    const defaults = limitsRes.ok ? ((await limitsRes.json()) as LimitDefaults) : null;
    const memory = memRes?.ok ? ((await memRes.json()).content as string) : '';
    const skills = skillsRes?.ok ? ((await skillsRes.json()) as SkillInfo[]) : [];
    return { tasks, defaults, memory, skills, up: tasksRes.ok };
  } catch {
    return { tasks: [], defaults: null, memory: '', skills: [], up: false };
  }
}

export default async function Home() {
  const { tasks, defaults, memory, skills, up } = await getData();
  const verifiedSkills = skills.filter((s) => s.verified);

  return (
    <main className="mx-auto max-w-3xl px-6 py-14">
      <header className="mb-8">
        <h1 className="text-2xl font-bold tracking-tight">{env.NEXT_PUBLIC_APP_NAME}</h1>
        <p className="mt-1 text-sm opacity-60">
          Publish a goal. The agent plans it, writes files and runs commands in its own sandboxed
          workspace, checks its own work, and keeps going until the goal is done — stopping the
          moment it hits a limit you set.
        </p>
      </header>

      <PublishForm defaults={defaults} skills={verifiedSkills} />

      {memory.trim() && (
        <details className="mt-6 rounded-xl border border-black/10 px-4 py-3 dark:border-white/10">
          <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide opacity-50">
            What the agent remembers
          </summary>
          <pre className="mt-2 max-h-60 overflow-auto whitespace-pre-wrap font-mono text-xs opacity-70">
            {memory}
          </pre>
        </details>
      )}

      <section className="mt-10">
        <h2 className="mb-3 text-sm font-semibold opacity-70">
          Your tasks {tasks.length > 0 && <span className="opacity-50">({tasks.length})</span>}
        </h2>

        {!up && (
          <p className="rounded-lg border border-amber-500/30 bg-amber-500/5 px-4 py-3 text-sm text-amber-700 dark:text-amber-400">
            The API isn’t reachable yet. Start it with <code className="font-mono">make up</code>{' '}
            (or <code className="font-mono">make dev</code>), then refresh.
          </p>
        )}

        {up && tasks.length === 0 && (
          <p className="rounded-lg border border-black/10 px-4 py-6 text-center text-sm opacity-50 dark:border-white/10">
            No tasks yet. Publish one above to watch the loop run.
          </p>
        )}

        <div className="grid gap-3">
          {tasks.map((task) => (
            <TaskCard key={task.id} task={task} />
          ))}
        </div>
      </section>
    </main>
  );
}
