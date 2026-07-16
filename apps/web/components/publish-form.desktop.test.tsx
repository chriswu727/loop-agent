import type { DesktopState, LoopDesktopApi } from '@repo/desktop-contract';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PublishForm } from './publish-form';

const { publish, push } = vi.hoisted(() => ({ publish: vi.fn(), push: vi.fn() }));

vi.mock('next/navigation', () => ({ useRouter: () => ({ push, refresh: vi.fn() }) }));
vi.mock('@/lib/api-client', () => ({
  ApiError: class ApiError extends Error {},
  tasksApi: { publish, start: vi.fn(), upload: vi.fn() },
}));

const state: DesktopState = {
  appVersion: '0.1.0',
  isDesktop: true,
  lastError: null,
  project: { name: 'loop-agent', relativePath: '.' },
  provider: 'anthropic',
  providerStorage: 'encrypted',
  recoveryRequired: false,
  runtime: 'ready',
};

function installBridge() {
  const bridge: LoopDesktopApi = {
    configureProvider: vi.fn(async () => state),
    getCachedState: () => state,
    getState: async () => state,
    onStateChange: () => () => undefined,
    openSettings: vi.fn(async () => state),
    restartRuntime: vi.fn(async () => state),
    selectProject: vi.fn(async () => state),
  };
  Object.defineProperty(window, 'loopDesktop', { configurable: true, value: bridge });
}

afterEach(() => {
  vi.clearAllMocks();
  Reflect.deleteProperty(window, 'loopDesktop');
});

describe('PublishForm desktop binding', () => {
  it('submits only the relative single-project path', async () => {
    installBridge();
    publish.mockResolvedValue({ id: 'task-1' });
    render(
      <PublishForm
        defaults={{
          local_projects_enabled: true,
          sibyl_available: false,
          argus_available: false,
          max_steps_cap: 40,
          max_steps_default: 12,
          token_budget_cap: 200000,
          token_budget_default: 60000,
        }}
        isDesktop
      />,
    );

    fireEvent.change(screen.getByLabelText('Publish a task'), {
      target: { value: 'Make the verified desktop change' },
    });
    fireEvent.change(screen.getByLabelText('Acceptance contract'), {
      target: { value: 'The requested change is present\nThe complete test suite passes' },
    });
    fireEvent.change(screen.getByLabelText('Required final artifacts'), {
      target: { value: 'dist/report.json\ndist/audit.log' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Run the agent/ }));

    await waitFor(() => expect(publish).toHaveBeenCalledOnce());
    expect(publish.mock.calls[0]?.[0]).toMatchObject({
      project_path: '.',
      required_artifacts: ['dist/report.json', 'dist/audit.log'],
      success_criteria: ['The requested change is present', 'The complete test suite passes'],
      verification_mode: 'strict',
    });
    expect(JSON.stringify(publish.mock.calls[0]?.[0])).not.toContain('/Users/');
    expect(push).toHaveBeenCalledWith('/tasks/task-1');
  });

  it('refuses submission until the desktop bridge confirms a ready runtime', async () => {
    render(
      <PublishForm
        defaults={{
          local_projects_enabled: true,
          sibyl_available: false,
          argus_available: false,
          max_steps_cap: 40,
          max_steps_default: 12,
          token_budget_cap: 200000,
          token_budget_default: 60000,
        }}
        isDesktop
      />,
    );

    fireEvent.change(screen.getByLabelText('Publish a task'), {
      target: { value: 'Do not race the desktop bridge' },
    });
    fireEvent.change(screen.getByLabelText('Acceptance contract'), {
      target: { value: 'The requested change is present' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Run the agent/ }));

    expect(await screen.findByText(/desktop runtime must be ready/i)).toBeInTheDocument();
    expect(publish).not.toHaveBeenCalled();
  });
});
