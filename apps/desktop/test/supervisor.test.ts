import { describe, expect, it, vi } from 'vitest';
import { RuntimeSupervisor, type CommandRunner } from '../src/supervisor.js';

const credentials = {
  apiToken: 'a'.repeat(43),
  provider: 'anthropic' as const,
  providerApiKey: 'provider-secret',
  providerStorage: 'encrypted' as const,
  secretKey: 's'.repeat(64),
};

const ownedServices = [
  'api',
  'browser-gateway',
  'calendar-gateway',
  'egress-proxy',
  'email-gateway',
  'postgres',
  'redis',
  'vision-gateway',
  'web',
  'worker',
].join('\n');

describe('desktop runtime supervisor', () => {
  it('starts Docker Compose without a shell and waits for both services', async () => {
    let healthy = false;
    const calls: Array<{ args: string[]; command: string }> = [];
    const environments: NodeJS.ProcessEnv[] = [];
    const commandRunner: CommandRunner = vi.fn(async (command, args, options) => {
      calls.push({ args, command });
      environments.push(options.env);
      if (args.includes('up')) healthy = true;
      return { stderr: '', stdout: '' };
    });
    const fetcher = (async (input: string | URL | Request, init?: RequestInit) => {
      if (!healthy) return new Response('', { status: 503 });
      const url = String(input);
      if (url.endsWith('/api/health')) return Response.json({ service: 'web' });
      if (url.endsWith('/healthz')) return Response.json({ service: 'api' });
      if (url.endsWith('/readyz')) return Response.json({ status: 'ready' });
      expect(new Headers(init?.headers).get('authorization')).toBe(
        `Bearer ${credentials.apiToken}`,
      );
      return Response.json({ max_steps_cap: 40, token_budget_cap: 200000 });
    }) as typeof fetch;
    const states: string[] = [];
    const supervisor = new RuntimeSupervisor({
      commandRunner,
      fetcher,
      healthTimeoutMs: 100,
      onChange: (phase) => states.push(phase),
      pollIntervalMs: 1,
      runtimeRoot: '/runtime',
      stateDirectory: '/state',
    });

    await supervisor.start('/private/project', credentials);

    expect(calls[0]).toMatchObject({
      command: process.platform === 'win32' ? 'docker.exe' : 'docker',
    });
    expect(calls.some((call) => call.args.includes('up') && call.args.includes('--detach'))).toBe(
      true,
    );
    expect(environments.at(-1)).toMatchObject({
      ANTHROPIC_API_KEY: 'provider-secret',
      DEEPSEEK_API_KEY: '',
      LLM_DEFAULT_PROVIDER: 'anthropic',
    });
    expect(states).toEqual(['starting', 'ready']);
  });

  it('redacts native paths from a failed start', async () => {
    const errors: Array<string | null> = [];
    const commandRunner: CommandRunner = async () => {
      throw new Error('cannot mount /private/project or /private/state with provider-secret');
    };
    const supervisor = new RuntimeSupervisor({
      commandRunner,
      fetcher: (async () => new Response('', { status: 503 })) as typeof fetch,
      onChange: (_phase, error) => errors.push(error),
      runtimeRoot: '/runtime',
      stateDirectory: '/private/state',
    });

    await expect(supervisor.start('/private/project', credentials)).rejects.toThrow(
      'cannot mount [selected project] or [desktop state] with [redacted]',
    );
    expect(errors.at(-1)).not.toContain('/private/project');
    expect(errors.at(-1)).not.toContain('/private/state');
    expect(errors.at(-1)).not.toContain('provider-secret');
  });

  it('does not adopt unrelated services that only return successful responses', async () => {
    let runtimeStarted = false;
    const commandRunner: CommandRunner = vi.fn(async (_command, args) => {
      if (args.includes('up')) runtimeStarted = true;
      return { stderr: '', stdout: args.includes('ps') ? ownedServices : '' };
    });
    const fetcher = (async (input: string | URL | Request) => {
      if (!runtimeStarted) return Response.json({ status: 'ok' });
      const url = String(input);
      if (url.endsWith('/api/health')) return Response.json({ service: 'web' });
      if (url.endsWith('/healthz')) return Response.json({ service: 'api' });
      if (url.endsWith('/readyz')) return Response.json({ status: 'ready' });
      return Response.json({ max_steps_cap: 40, token_budget_cap: 200000 });
    }) as typeof fetch;
    const supervisor = new RuntimeSupervisor({
      commandRunner,
      fetcher,
      healthTimeoutMs: 100,
      onChange: () => undefined,
      pollIntervalMs: 1,
      runtimeRoot: '/runtime',
      stateDirectory: '/state',
    });

    await supervisor.start('/private/project', credentials);

    expect(commandRunner).toHaveBeenCalledTimes(3);
    expect(commandRunner).toHaveBeenCalledWith(
      expect.any(String),
      expect.arrayContaining(['up', '--detach']),
      expect.any(Object),
    );
  });

  it('adopts a healthy runtime only after Compose ownership is verified', async () => {
    const commandRunner: CommandRunner = vi.fn(async (_command, args) => ({
      stderr: '',
      stdout: args.includes('ps') ? ownedServices : '',
    }));
    const fetcher = (async (input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith('/api/health')) return Response.json({ service: 'web' });
      if (url.endsWith('/healthz')) return Response.json({ service: 'api' });
      if (url.endsWith('/readyz')) return Response.json({ status: 'ready' });
      return Response.json({ max_steps_cap: 40, token_budget_cap: 200000 });
    }) as typeof fetch;
    const supervisor = new RuntimeSupervisor({
      commandRunner,
      fetcher,
      onChange: () => undefined,
      runtimeRoot: '/runtime',
      stateDirectory: '/state',
    });

    await supervisor.start('/private/project', credentials);

    expect(commandRunner).toHaveBeenCalledTimes(2);
    expect(commandRunner).not.toHaveBeenCalledWith(
      expect.any(String),
      expect.arrayContaining(['up']),
      expect.any(Object),
    );
  });
});
