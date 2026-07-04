import type { Task } from '@repo/api-contract';
import type { ReactNode } from 'react';

// A least-authority runtime should let you SEE the authority a task actually ran
// with — not just claim it. This surfaces the granted capabilities: network reach
// (default-deny unless opted in, narrowed to an allowlist when set), tool set, and
// any elevated capabilities (browser/email/calendar/skill).
const TONES = {
  safe: 'bg-green-500/15 text-green-600 dark:text-green-400',
  elevated: 'bg-amber-500/15 text-amber-600 dark:text-amber-400',
  neutral: 'bg-black/5 opacity-70 dark:bg-white/10',
  cap: 'bg-blue-500/15 text-blue-600 dark:text-blue-400',
  skill: 'bg-purple-500/15 text-purple-600 dark:text-purple-400',
} as const;

function Pill({ tone, children }: { tone: keyof typeof TONES; children: ReactNode }) {
  return <span className={`rounded-md px-2 py-0.5 font-medium ${TONES[tone]}`}>{children}</span>;
}

export function AuthorityPanel({ task }: { task: Task }) {
  const network = !task.allow_egress
    ? { label: 'Network: none (default-deny)', tone: 'safe' as const }
    : task.egress_hosts && task.egress_hosts.length > 0
      ? { label: `Network: ${task.egress_hosts.join(', ')}`, tone: 'elevated' as const }
      : { label: 'Network: any host', tone: 'elevated' as const };

  return (
    <section className="mt-6 rounded-2xl border border-black/10 bg-white/40 p-5 dark:border-white/10 dark:bg-white/[0.02]">
      <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide opacity-50">
        Granted authority
      </h2>
      <div className="flex flex-wrap gap-2 text-xs">
        <Pill tone={network.tone}>{network.label}</Pill>
        <Pill tone="neutral">
          {task.allowed_tools && task.allowed_tools.length > 0
            ? `Tools: ${task.allowed_tools.join(', ')}`
            : 'Tools: all default'}
        </Pill>
        {task.require_approval && <Pill tone="neutral">Approval required</Pill>}
        {task.use_browser && <Pill tone="cap">Browser</Pill>}
        {task.use_email && <Pill tone="cap">Email</Pill>}
        {task.use_calendar && <Pill tone="cap">Calendar</Pill>}
        {task.skill && <Pill tone="skill">Skill: {task.skill}</Pill>}
      </div>
    </section>
  );
}
