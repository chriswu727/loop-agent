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
  const granted = new Set(task.authority.resolved);
  const emailGranted = granted.has('email.read') || granted.has('email.send');
  const calendarGranted = granted.has('calendar.read') || granted.has('calendar.write');
  const visionGranted = granted.has('vision');
  const networkedAuthority =
    granted.has('net.shell') ||
    granted.has('net.browser') ||
    emailGranted ||
    calendarGranted ||
    visionGranted;
  const shellNetwork = !granted.has('net.shell')
    ? { label: 'Shell network: denied', tone: 'safe' as const }
    : task.authority.egress_hosts.length > 0
      ? {
          label: `Shell network: ${task.authority.egress_hosts.join(', ')}`,
          tone: 'elevated' as const,
        }
      : { label: 'Shell network: invalid empty policy', tone: 'elevated' as const };
  const allowed = task.authority.audit.filter((event) => event.decision === 'allowed').length;
  const blocked = task.authority.audit.filter((event) => event.decision === 'blocked').length;

  return (
    <section className="mt-6 rounded-2xl border border-black/10 bg-white/40 p-5 dark:border-white/10 dark:bg-white/[0.02]">
      <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide opacity-50">
        Granted authority
      </h2>
      <div className="flex flex-wrap gap-2 text-xs">
        <Pill tone={shellNetwork.tone}>{shellNetwork.label}</Pill>
        <Pill tone="neutral">Schema: {task.authority.schema}</Pill>
        {task.authority.resolved.map((capability) => (
          <Pill key={capability} tone={capability.startsWith('net.') ? 'elevated' : 'cap'}>
            {capability}
          </Pill>
        ))}
        {task.require_approval && <Pill tone="neutral">Approval required</Pill>}
        {task.authority.sandbox && <Pill tone="neutral">Sandbox: {task.authority.sandbox}</Pill>}
        {networkedAuthority && task.authority.enforcement.egress_proxy && (
          <Pill tone="safe">Destination proxy enforced</Pill>
        )}
        {granted.has('net.browser') && task.authority.enforcement.browser_gateway && (
          <Pill tone="safe">Browser network isolated</Pill>
        )}
        {emailGranted && task.authority.enforcement.email_gateway && (
          <Pill tone="safe">Email network isolated</Pill>
        )}
        {calendarGranted && task.authority.enforcement.calendar_gateway && (
          <Pill tone="safe">Calendar network isolated</Pill>
        )}
        {visionGranted && task.authority.enforcement.vision_gateway && (
          <Pill tone="safe">Vision network isolated</Pill>
        )}
        {task.authority.audit.length > 0 && (
          <Pill tone="neutral">
            Runtime decisions: {allowed} allowed / {blocked} blocked
          </Pill>
        )}
        {task.skill && <Pill tone="skill">Skill: {task.skill}</Pill>}
      </div>
    </section>
  );
}
