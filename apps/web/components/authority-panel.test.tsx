import { render, screen } from '@testing-library/react';
import type { Task } from '@repo/api-contract';
import { describe, expect, it } from 'vitest';
import { AuthorityPanel } from './authority-panel';
import { ContractPanel } from './contract-panel';

const baseTask: Task = {
  id: 'task-1',
  goal: 'Visit an example page',
  owner_id: 'owner-1',
  project_id: 'default',
  status: 'completed',
  loop: { state: 'completed', transition_reason: 'verification_accepted', sequence: 4 },
  rubric: [],
  criteria_source: 'generated',
  verification_mode: 'judgment',
  required_checks: [],
  baseline_checks: [],
  contract: null,
  contract_hash: null,
  contract_status: 'not_required',
  pending_question: null,
  allowed_tools: null,
  authority: {
    schema: 'loop.capabilities/v1',
    requested: ['net.browser'],
    resolved: ['net.browser'],
    egress_hosts: [],
    sandbox: 'required',
    enforcement: {
      provider_gateway: true,
      browser_gateway: true,
      email_gateway: true,
      calendar_gateway: true,
      vision_gateway: true,
      egress_proxy: true,
    },
    audit: [],
  },
  allow_egress: false,
  egress_hosts: null,
  require_approval: false,
  use_browser: true,
  use_email: false,
  use_calendar: false,
  use_vision: false,
  skill: null,
  parent_id: null,
  depth: 0,
  idempotency_key: 'publish-1',
  attempt: 1,
  limits: { max_steps: 20, token_budget: 20_000 },
  summary: null,
  verification_score: 100,
  verified_by: 'execution',
  receipt_hash: 'abc',
  sandbox: 'required',
  steps_used: 1,
  tokens_used: 10,
  change_set: null,
  stop_reason: 'goal_achieved',
  error: null,
  created_at: '2026-07-14T00:00:00Z',
  updated_at: '2026-07-14T00:00:01Z',
};

describe('AuthorityPanel', () => {
  it('does not present browser authority as shell network authority', () => {
    render(<AuthorityPanel task={baseTask} />);

    expect(screen.getByText('Shell network: denied')).toBeInTheDocument();
    expect(screen.getByText('net.browser')).toBeInTheDocument();
    expect(screen.getByText('Browser network isolated')).toBeInTheDocument();
    expect(screen.queryByText('Shell network: any host')).not.toBeInTheDocument();
  });

  it('shows the effective shell allowlist', () => {
    render(
      <AuthorityPanel
        task={{
          ...baseTask,
          authority: {
            ...baseTask.authority,
            resolved: ['exec', 'net.shell'],
            egress_hosts: ['api.example.com'],
          },
        }}
      />,
    );

    expect(screen.getByText('Shell network: api.example.com')).toBeInTheDocument();
    expect(screen.getByText('Destination proxy enforced')).toBeInTheDocument();
  });

  it('shows each isolated provider network that the task actually used', () => {
    render(
      <AuthorityPanel
        task={{
          ...baseTask,
          authority: {
            ...baseTask.authority,
            resolved: ['email.read', 'calendar.write', 'vision'],
          },
        }}
      />,
    );

    expect(screen.getByText('Destination proxy enforced')).toBeInTheDocument();
    expect(screen.getByText('Email network isolated')).toBeInTheDocument();
    expect(screen.getByText('Calendar network isolated')).toBeInTheDocument();
    expect(screen.getByText('Vision network isolated')).toBeInTheDocument();
  });

  it('summarizes runtime enforcement decisions', () => {
    render(
      <AuthorityPanel
        task={{
          ...baseTask,
          authority: {
            ...baseTask.authority,
            audit: [
              { kind: 'provider', decision: 'allowed', tool: 'browser_navigate' },
              { kind: 'egress', decision: 'blocked', host: 'evil.example' },
            ],
          },
        }}
      />,
    );

    expect(screen.getByText('Runtime decisions: 1 allowed / 1 blocked')).toBeInTheDocument();
  });
});

describe('ContractPanel', () => {
  it('shows a compiled contract hash, critic result, and repository evidence', () => {
    render(
      <ContractPanel
        task={{
          ...baseTask,
          criteria_source: 'compiled',
          verification_mode: 'strict',
          contract_status: 'locked',
          contract_hash: '0123456789abcdef',
          contract: {
            schema_version: 'loop.contract-draft/v1',
            compiler: { provider: 'fixture', model: 'contract-v1' },
            criteria: ['The greeting is updated'],
            checks: [
              {
                id: 'contract-001',
                kind: 'file_contains',
                command: null,
                path: 'app.ts',
                text: 'hello',
                expect_exit: 0,
                expect_stdout: null,
                criterion_ids: ['criterion-001'],
                source: 'contract',
              },
            ],
            artifacts: ['app.ts'],
            risk: 'low',
            assumptions: ['The greeting is local text.'],
            confidence: 96,
            authority_requests: [],
            discovery: {
              manifests: ['package.json'],
              scripts: { test: 'vitest run' },
              test_files: ['app.test.ts'],
              build_outputs: [],
              quality_checks: [],
              files_scanned: 12,
              truncated: false,
            },
            clarifications: [],
            critique: {
              accepted: true,
              issues: [],
              question: null,
              provider: 'fixture',
              model: 'critic-v1',
            },
          },
        }}
      />,
    );

    expect(screen.getByText('Loop compiled')).toBeInTheDocument();
    expect(screen.getByText('locked 0123456789ab')).toBeInTheDocument();
    expect(screen.getByText('The greeting is updated')).toBeInTheDocument();
    expect(screen.getByText(/12 files discovered/)).toBeInTheDocument();
    expect(screen.getByText('compiled by fixture/contract-v1')).toBeInTheDocument();
    expect(screen.getByText('criticized by fixture/critic-v1')).toBeInTheDocument();
  });
});
