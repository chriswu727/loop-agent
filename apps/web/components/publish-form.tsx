'use client';

import type { Capability, LimitDefaults, SkillInfo } from '@repo/api-contract';
import { useRouter } from 'next/navigation';
import { useRef, useState } from 'react';
import { ApiError, tasksApi } from '@/lib/api-client';
import { useDesktopState } from '@/lib/desktop';

const FALLBACK: LimitDefaults = {
  max_steps_default: 12,
  max_steps_cap: 40,
  token_budget_default: 60000,
  token_budget_cap: 200000,
  local_projects_enabled: false,
};

const EXAMPLES = [
  {
    goal: 'Write a Python script that prints the first 15 Fibonacci numbers, then run it to confirm the output.',
    criteria:
      'A runnable Python script is added.\nThe script prints exactly the first 15 Fibonacci numbers.',
  },
  {
    goal: 'Attach a spreadsheet, then: add a Total column summing each row, and save it.',
    criteria: 'The workbook opens successfully.\nEvery data row has the correct Total formula.',
  },
  {
    goal: 'Attach a .docx, then: fix typos and add a one-paragraph summary at the top.',
    criteria:
      'The edited document opens successfully.\nA summary paragraph appears before the original content.',
  },
];

export function PublishForm({
  defaults,
  skills = [],
  isDesktop = false,
}: {
  defaults: LimitDefaults | null;
  skills?: SkillInfo[];
  isDesktop?: boolean;
}) {
  const d = defaults ?? FALLBACK;
  const router = useRouter();
  const desktopState = useDesktopState();

  const [goal, setGoal] = useState('');
  const [successCriteria, setSuccessCriteria] = useState('');
  const [verificationCommands, setVerificationCommands] = useState('');
  const [maxSteps, setMaxSteps] = useState(d.max_steps_default);
  const [tokenBudget, setTokenBudget] = useState(d.token_budget_default);
  const [files, setFiles] = useState<File[]>([]);
  const [projectPath, setProjectPath] = useState(isDesktop ? '.' : '');
  const [noShell, setNoShell] = useState(false);
  const [allowNetwork, setAllowNetwork] = useState(false);
  const [egressHosts, setEgressHosts] = useState('');
  const [requireApproval, setRequireApproval] = useState(false);
  const [useBrowser, setUseBrowser] = useState(false);
  const [useEmail, setUseEmail] = useState(false);
  const [useCalendar, setUseCalendar] = useState(false);
  const [useVision, setUseVision] = useState(false);
  const [skill, setSkill] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const idempotencyKey = useRef(crypto.randomUUID());
  const needsDestinations = allowNetwork || useBrowser;
  const hasProject = Boolean(projectPath.trim());
  const criteria = successCriteria
    .split('\n')
    .map((item) => item.trim())
    .filter(Boolean);

  async function chooseDesktopProject() {
    setError(null);
    const state = await window.loopDesktop?.selectProject();
    if (state?.project) {
      setProjectPath(state.project.relativePath);
      router.refresh();
    }
    if (state?.lastError) setError(state.lastError);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (goal.trim().length < 4 || submitting) return;
    if (hasProject && criteria.length === 0) {
      setError('Local project tasks require at least one explicit success criterion.');
      return;
    }
    if (needsDestinations && !egressHosts.trim()) {
      setError('Shell and browser network access require at least one destination host.');
      return;
    }
    if (isDesktop && desktopState?.runtime !== 'ready') {
      setError('The desktop runtime must be ready before publishing a project task.');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const limits = { max_steps: maxSteps, token_budget: tokenBudget };
      const capabilities: Capability[] = [
        'fs.read',
        'fs.write',
        'memory.read',
        'memory.write',
        'task.spawn',
      ];
      if (!noShell) capabilities.push('exec');
      if (allowNetwork) capabilities.push('net.shell');
      if (useBrowser) capabilities.push('net.browser');
      if (useEmail) capabilities.push('email.read', 'email.send');
      if (useCalendar) capabilities.push('calendar.read', 'calendar.write');
      if (useVision) capabilities.push('vision');
      const base = {
        goal: goal.trim(),
        success_criteria: criteria.length > 0 ? criteria : null,
        verification_commands: verificationCommands
          .split('\n')
          .map((item) => item.trim())
          .filter(Boolean),
        verification_mode:
          hasProject || criteria.length > 0 ? ('strict' as const) : ('judgment' as const),
        project_path: projectPath.trim() || null,
        limits,
        capabilities,
        egress_hosts:
          needsDestinations && egressHosts.trim()
            ? egressHosts
                .split(',')
                .map((h) => h.trim())
                .filter(Boolean)
            : null,
        require_approval: requireApproval,
        skill: skill || null,
        idempotency_key: idempotencyKey.current,
      };
      if (files.length > 0) {
        // Draft first so files land in the workspace, then start the agent.
        const task = await tasksApi.publish({ ...base, autostart: false });
        for (const file of files) await tasksApi.upload(task.id, file);
        await tasksApi.start(task.id);
        router.push(`/tasks/${task.id}`);
      } else {
        const task = await tasksApi.publish(base);
        router.push(`/tasks/${task.id}`);
      }
      idempotencyKey.current = crypto.randomUUID();
    } catch (err) {
      const message =
        err instanceof ApiError ? err.message : 'Could not reach the API. Is it running?';
      setError(message);
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      className="rounded-2xl border border-black/10 bg-white/60 p-5 shadow-sm dark:border-white/10 dark:bg-white/[0.03]"
    >
      <label htmlFor="goal" className="text-sm font-medium opacity-80">
        Publish a task
      </label>
      <textarea
        id="goal"
        value={goal}
        onChange={(e) => setGoal(e.target.value)}
        placeholder="Describe a goal. The agent will plan it, write files and run commands in its own workspace, check its own work, and keep going until it's done."
        rows={3}
        className="mt-2 w-full resize-y rounded-lg border border-black/10 bg-transparent px-3 py-2 text-sm outline-none focus:border-blue-500/60 dark:border-white/15"
      />

      <div className="mt-2 flex flex-wrap gap-2">
        {EXAMPLES.map((example) => (
          <button
            key={example.goal}
            type="button"
            onClick={() => {
              setGoal(example.goal);
              setSuccessCriteria(example.criteria);
            }}
            className="rounded-full border border-black/10 px-2.5 py-1 text-xs opacity-70 hover:opacity-100 dark:border-white/15"
          >
            {example.goal.length > 42 ? `${example.goal.slice(0, 42)}…` : example.goal}
          </button>
        ))}
      </div>

      <div className="mt-4 rounded-xl border border-blue-500/20 bg-blue-500/[0.04] p-3">
        <label
          htmlFor="success-criteria"
          className="text-xs font-semibold text-blue-700 dark:text-blue-300"
        >
          Acceptance contract {hasProject ? '(required)' : '(optional)'}
        </label>
        <textarea
          id="success-criteria"
          aria-label="Acceptance contract"
          value={successCriteria}
          onChange={(event) => setSuccessCriteria(event.target.value)}
          required={hasProject}
          rows={3}
          placeholder="One concrete success criterion per line"
          className="mt-2 w-full resize-y rounded-lg border border-black/10 bg-transparent px-3 py-2 text-xs outline-none focus:border-blue-500/60 dark:border-white/15"
        />
        <label
          htmlFor="verification-commands"
          className="mt-3 block text-xs font-medium opacity-70"
        >
          Required verification commands <span className="font-normal opacity-60">(optional)</span>
        </label>
        <textarea
          id="verification-commands"
          aria-label="Required verification commands"
          value={verificationCommands}
          onChange={(event) => setVerificationCommands(event.target.value)}
          rows={2}
          placeholder="One command per line, e.g. pnpm test"
          className="mt-1 w-full resize-y rounded-lg border border-black/10 bg-transparent px-3 py-2 font-mono text-xs outline-none focus:border-blue-500/60 dark:border-white/15"
        />
        <p className="mt-2 text-[11px] opacity-55">
          In strict mode, every criterion needs passing execution evidence. Loop also discovers
          repository quality gates and compares them with the pre-change baseline.
        </p>
      </div>

      {d.local_projects_enabled && (
        <div className="mt-3">
          <label htmlFor="project-path" className="text-xs font-medium opacity-70">
            Local Git project <span className="font-normal opacity-60">(optional)</span>
          </label>
          {isDesktop ? (
            <div className="mt-1 flex items-center gap-2">
              <input
                id="project-path"
                readOnly
                value={desktopState?.project?.name ?? 'Selected desktop project'}
                className="min-w-0 flex-1 truncate rounded-lg border border-black/10 bg-black/[0.02] px-3 py-1.5 text-xs dark:border-white/15 dark:bg-white/[0.03]"
              />
              <button
                type="button"
                onClick={() => void chooseDesktopProject()}
                className="rounded-lg border border-black/10 px-3 py-1.5 text-xs font-medium hover:bg-black/5 dark:border-white/15 dark:hover:bg-white/5"
              >
                Change…
              </button>
            </div>
          ) : (
            <input
              id="project-path"
              value={projectPath}
              onChange={(event) => setProjectPath(event.target.value)}
              placeholder="Path relative to LOOP_LOCAL_PROJECTS_ROOT"
              className="mt-1 w-full rounded-lg border border-black/10 bg-transparent px-3 py-1.5 text-xs outline-none focus:border-blue-500/60 dark:border-white/15"
            />
          )}
          <p className="mt-1 text-[11px] opacity-50">
            Loop requires a clean repository, works in an isolated clone, and applies only the
            Receipt-verified patch you approve.
          </p>
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-3 text-xs">
        <label className="cursor-pointer rounded-lg border border-black/10 px-3 py-1.5 opacity-80 hover:opacity-100 dark:border-white/15">
          Attach files
          <input
            type="file"
            multiple
            className="hidden"
            onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
          />
        </label>
        {files.map((f) => (
          <span
            key={f.name}
            className="rounded-full bg-blue-500/10 px-2 py-1 text-blue-600 dark:text-blue-400"
          >
            {f.name}
          </span>
        ))}
        {files.length > 0 && (
          <button
            type="button"
            onClick={() => setFiles([])}
            className="opacity-50 hover:opacity-100"
          >
            clear
          </button>
        )}
        <label className="ml-auto flex cursor-pointer items-center gap-1.5 opacity-80">
          <input type="checkbox" checked={noShell} onChange={(e) => setNoShell(e.target.checked)} />
          No shell (files only)
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
        <label className="flex cursor-pointer items-center gap-1.5 opacity-80">
          <input
            type="checkbox"
            checked={useBrowser}
            onChange={(e) => setUseBrowser(e.target.checked)}
          />
          Use browser
        </label>
        <label className="flex cursor-pointer items-center gap-1.5 opacity-80">
          <input
            type="checkbox"
            checked={useEmail}
            onChange={(e) => setUseEmail(e.target.checked)}
          />
          Use email
        </label>
        <label className="flex cursor-pointer items-center gap-1.5 opacity-80">
          <input
            type="checkbox"
            checked={useCalendar}
            onChange={(e) => setUseCalendar(e.target.checked)}
          />
          Use calendar
        </label>
        <label className="flex cursor-pointer items-center gap-1.5 opacity-80">
          <input
            type="checkbox"
            checked={useVision}
            onChange={(e) => setUseVision(e.target.checked)}
          />
          Use vision
        </label>
        {skills.length > 0 && (
          <select
            value={skill}
            onChange={(e) => setSkill(e.target.value)}
            className="rounded-lg border border-black/10 bg-transparent px-2 py-1 dark:border-white/15"
          >
            <option value="">No skill</option>
            {skills.map((s) => (
              <option key={s.name} value={s.name}>
                Skill: {s.name}
              </option>
            ))}
          </select>
        )}
      </div>

      {needsDestinations && (
        <input
          type="text"
          aria-label="Allowed destination hosts"
          value={egressHosts}
          onChange={(e) => setEgressHosts(e.target.value)}
          required
          placeholder="Required destinations (comma-separated, e.g. api.github.com, pypi.org)"
          className="mt-3 w-full rounded-lg border border-black/10 bg-transparent px-3 py-1.5 text-xs outline-none focus:border-blue-500/60 dark:border-white/15"
        />
      )}

      <div className="mt-5 grid gap-5 sm:grid-cols-2">
        <Slider
          label="Max steps"
          value={maxSteps}
          min={1}
          max={d.max_steps_cap}
          step={1}
          onChange={setMaxSteps}
          display={(v) => `${v}`}
        />
        <Slider
          label="Token budget"
          value={tokenBudget}
          min={5000}
          max={d.token_budget_cap}
          step={5000}
          onChange={setTokenBudget}
          display={(v) => `${Math.round(v / 1000)}k`}
        />
      </div>

      {error && <p className="mt-4 text-sm text-red-600 dark:text-red-400">{error}</p>}

      <div className="mt-5 flex items-center justify-between">
        <p className="text-xs opacity-50">
          The agent stops when the goal is verified, or it runs out of steps or budget.
        </p>
        <button
          type="submit"
          disabled={
            goal.trim().length < 4 ||
            submitting ||
            (hasProject && criteria.length === 0) ||
            (needsDestinations && !egressHosts.trim())
          }
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {submitting ? 'Starting…' : 'Run the agent'}
        </button>
      </div>
    </form>
  );
}

function Slider({
  label,
  value,
  min,
  max,
  step,
  onChange,
  display,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  display: (v: number) => string;
}) {
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between">
        <span className="text-xs font-medium opacity-70">{label}</span>
        <span className="text-xs font-semibold tabular-nums">{display(value)}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-blue-600"
      />
    </div>
  );
}
