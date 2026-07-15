import type { DesktopState, LoopDesktopApi } from '@repo/desktop-contract';
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { DesktopRuntimeBar } from './desktop-runtime-bar';

function installBridge(state: DesktopState) {
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
  return bridge;
}

afterEach(() => {
  Reflect.deleteProperty(window, 'loopDesktop');
});

describe('DesktopRuntimeBar', () => {
  it('shows verified runtime and the authorized project', () => {
    installBridge({
      appVersion: '0.1.0',
      isDesktop: true,
      lastError: null,
      project: { name: 'loop-agent', relativePath: '.' },
      provider: 'anthropic',
      providerStorage: 'encrypted',
      recoveryRequired: false,
      runtime: 'ready',
    });
    render(<DesktopRuntimeBar />);
    expect(screen.getByText(/Desktop runtime verified/)).toHaveTextContent('loop-agent');
  });

  it('offers recovery when an interrupted session needs attention', async () => {
    const bridge = installBridge({
      appVersion: '0.1.0',
      isDesktop: true,
      lastError: 'runtime unavailable',
      project: { name: 'loop-agent', relativePath: '.' },
      provider: 'anthropic',
      providerStorage: 'encrypted',
      recoveryRequired: true,
      runtime: 'failed',
    });
    render(<DesktopRuntimeBar />);
    screen.getByRole('button', { name: 'Check and restart' }).click();
    expect(bridge.restartRuntime).toHaveBeenCalledOnce();
  });
});
