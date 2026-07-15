import { spawn } from 'node:child_process';
import { statSync } from 'node:fs';
import path from 'node:path';
import type { DesktopRuntimePhase } from '@repo/desktop-contract';
import type { RuntimeCredentials } from './store.js';

interface CommandResult {
  stderr: string;
  stdout: string;
}

export type CommandRunner = (
  command: string,
  args: string[],
  options: { cwd: string; env: NodeJS.ProcessEnv; timeoutMs: number },
) => Promise<CommandResult>;

interface SupervisorOptions {
  commandRunner?: CommandRunner;
  fetcher?: typeof fetch;
  healthTimeoutMs?: number;
  onChange: (phase: DesktopRuntimePhase, error: string | null) => void;
  pollIntervalMs?: number;
  runtimeRoot: string;
  stateDirectory: string;
}

const MAX_CAPTURE_BYTES = 64 * 1024;
const OWNED_RUNTIME_SERVICES = [
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
] as const;

function appendBounded(current: string, value: Buffer): string {
  const combined = `${current}${value.toString('utf8')}`;
  return combined.length > MAX_CAPTURE_BYTES ? combined.slice(-MAX_CAPTURE_BYTES) : combined;
}

async function runCommand(
  command: string,
  args: string[],
  options: { cwd: string; env: NodeJS.ProcessEnv; timeoutMs: number },
): Promise<CommandResult> {
  return await new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: options.cwd,
      env: options.env,
      shell: false,
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk: Buffer) => {
      stdout = appendBounded(stdout, chunk);
    });
    child.stderr.on('data', (chunk: Buffer) => {
      stderr = appendBounded(stderr, chunk);
    });
    const timer = setTimeout(() => {
      child.kill('SIGTERM');
      reject(new Error(`${command} timed out`));
    }, options.timeoutMs);
    child.once('error', (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.once('exit', (code) => {
      clearTimeout(timer);
      if (code === 0) resolve({ stderr, stdout });
      else reject(new Error(stderr.trim() || stdout.trim() || `${command} exited with ${code}`));
    });
  });
}

export class RuntimeSupervisor {
  private readonly commandRunner: CommandRunner;
  private readonly fetcher: typeof fetch;
  private readonly healthTimeoutMs: number;
  private readonly onChange: SupervisorOptions['onChange'];
  private readonly pollIntervalMs: number;
  private readonly runtimeRoot: string;
  private readonly stateDirectory: string;
  private queue: Promise<void> = Promise.resolve();

  constructor(options: SupervisorOptions) {
    this.commandRunner = options.commandRunner ?? runCommand;
    this.fetcher = options.fetcher ?? fetch;
    this.healthTimeoutMs = options.healthTimeoutMs ?? 12 * 60 * 1000;
    this.onChange = options.onChange;
    this.pollIntervalMs = options.pollIntervalMs ?? 2_000;
    this.runtimeRoot = options.runtimeRoot;
    this.stateDirectory = options.stateDirectory;
  }

  start(projectPath: string, credentials: RuntimeCredentials): Promise<void> {
    return this.enqueue(async () => this.startInternal(projectPath, credentials));
  }

  stop(projectPath: string | null, credentials: RuntimeCredentials | null): Promise<void> {
    return this.enqueue(async () => this.stopInternal(projectPath, credentials));
  }

  restart(projectPath: string, credentials: RuntimeCredentials): Promise<void> {
    return this.enqueue(async () => {
      await this.stopInternal(projectPath, credentials);
      await this.startInternal(projectPath, credentials);
    });
  }

  private enqueue(action: () => Promise<void>): Promise<void> {
    const result = this.queue.then(action, action);
    this.queue = result.catch(() => undefined);
    return result;
  }

  private async startInternal(projectPath: string, credentials: RuntimeCredentials): Promise<void> {
    this.onChange('starting', null);
    const environment = this.environment(projectPath, credentials);
    try {
      await this.commandRunner(this.dockerCommand(), ['info'], {
        cwd: this.runtimeRoot,
        env: environment,
        timeoutMs: 15_000,
      });
      if ((await this.ownedRuntimeIsRunning(environment)) && (await this.isHealthy(credentials))) {
        this.onChange('ready', null);
        return;
      }
      await this.commandRunner(
        this.dockerCommand(),
        [...this.composeArgs(), 'up', '--build', '--detach', '--remove-orphans'],
        {
          cwd: this.runtimeRoot,
          env: environment,
          timeoutMs: 12 * 60 * 1000,
        },
      );
      await this.waitUntilHealthy(credentials);
      this.onChange('ready', null);
    } catch (error) {
      const message = this.sanitizeError(error, projectPath, credentials);
      this.onChange('failed', message);
      throw new Error(message);
    }
  }

  private async stopInternal(
    projectPath: string | null,
    credentials: RuntimeCredentials | null,
  ): Promise<void> {
    if (!projectPath || !credentials) {
      this.onChange('stopped', null);
      return;
    }
    this.onChange('stopping', null);
    try {
      await this.commandRunner(
        this.dockerCommand(),
        [...this.composeArgs(), 'down', '--timeout', '10'],
        {
          cwd: this.runtimeRoot,
          env: this.environment(projectPath, credentials),
          timeoutMs: 60_000,
        },
      );
      this.onChange('stopped', null);
    } catch (error) {
      const message = this.sanitizeError(error, projectPath, credentials);
      this.onChange('failed', message);
      throw new Error(message);
    }
  }

  private composeArgs(): string[] {
    return [
      'compose',
      '--project-name',
      'loop-desktop',
      '--project-directory',
      this.runtimeRoot,
      '--file',
      path.join(this.runtimeRoot, 'docker-compose.yml'),
      '--file',
      path.join(this.runtimeRoot, 'infra', 'desktop', 'docker-compose.yml'),
    ];
  }

  private environment(projectPath: string, credentials: RuntimeCredentials): NodeJS.ProcessEnv {
    let dockerGid = 0;
    try {
      dockerGid = statSync('/var/run/docker.sock').gid;
    } catch {
      dockerGid = 0;
    }
    const environment: NodeJS.ProcessEnv = {
      ...process.env,
      ANTHROPIC_API_KEY: '',
      COMPOSE_PROJECT_NAME: 'loop-desktop',
      LOOP_DESKTOP_API_TOKEN: credentials.apiToken,
      LOOP_DESKTOP_DOCKER_GID: String(dockerGid),
      LOOP_DESKTOP_HOST_GID: String(process.getgid?.() ?? 10001),
      LOOP_DESKTOP_HOST_UID: String(process.getuid?.() ?? 10001),
      LOOP_DESKTOP_PROJECT_SOURCE: projectPath,
      LOOP_DESKTOP_SECRET_KEY: credentials.secretKey,
      LOOP_DESKTOP_STATE_DIR: this.stateDirectory,
      DEEPSEEK_API_KEY: '',
      GEMINI_API_KEY: '',
      GLM_API_KEY: '',
      LLM_DEFAULT_PROVIDER: credentials.provider ?? 'deepseek',
      PROVIDER_GATEWAY_GEMINI_API_KEY: '',
    };
    if (credentials.provider && credentials.providerApiKey) {
      const key = `${credentials.provider.toUpperCase()}_API_KEY`;
      environment[key] = credentials.providerApiKey;
      if (credentials.provider === 'gemini') {
        environment.PROVIDER_GATEWAY_GEMINI_API_KEY = credentials.providerApiKey;
      }
    }
    return environment;
  }

  private dockerCommand(): string {
    return process.platform === 'win32' ? 'docker.exe' : 'docker';
  }

  private async ownedRuntimeIsRunning(environment: NodeJS.ProcessEnv): Promise<boolean> {
    try {
      const result = await this.commandRunner(
        this.dockerCommand(),
        [...this.composeArgs(), 'ps', '--services', '--status', 'running'],
        {
          cwd: this.runtimeRoot,
          env: environment,
          timeoutMs: 15_000,
        },
      );
      const running = new Set(result.stdout.split(/\r?\n/u).filter(Boolean));
      return OWNED_RUNTIME_SERVICES.every((service) => running.has(service));
    } catch {
      return false;
    }
  }

  private async isHealthy(credentials: RuntimeCredentials): Promise<boolean> {
    try {
      const signal = AbortSignal.timeout(2_000);
      const [web, apiIdentity, apiReadiness, limits] = await Promise.all([
        this.fetcher('http://127.0.0.1:3000/api/health', { signal }),
        this.fetcher('http://127.0.0.1:8000/healthz', { signal }),
        this.fetcher('http://127.0.0.1:8000/readyz', { signal }),
        this.fetcher('http://127.0.0.1:8000/api/v1/tasks/limits', {
          headers: { authorization: `Bearer ${credentials.apiToken}` },
          signal,
        }),
      ]);
      if (!web.ok || !apiIdentity.ok || !apiReadiness.ok || !limits.ok) return false;
      const [webBody, apiBody, readinessBody, limitsBody] = (await Promise.all([
        web.json(),
        apiIdentity.json(),
        apiReadiness.json(),
        limits.json(),
      ])) as Array<Record<string, unknown>>;
      return (
        webBody?.service === 'web' &&
        apiBody?.service === 'api' &&
        readinessBody?.status === 'ready' &&
        typeof limitsBody?.max_steps_cap === 'number' &&
        typeof limitsBody?.token_budget_cap === 'number'
      );
    } catch {
      return false;
    }
  }

  private async waitUntilHealthy(credentials: RuntimeCredentials): Promise<void> {
    const deadline = Date.now() + this.healthTimeoutMs;
    while (Date.now() < deadline) {
      if (await this.isHealthy(credentials)) return;
      await new Promise((resolve) => setTimeout(resolve, this.pollIntervalMs));
    }
    throw new Error('Loop local services did not become ready before the startup deadline.');
  }

  private sanitizeError(
    error: unknown,
    projectPath: string,
    credentials: RuntimeCredentials,
  ): string {
    const raw = error instanceof Error ? error.message : 'Loop local services failed to start.';
    let sanitized = raw
      .replaceAll(projectPath, '[selected project]')
      .replaceAll(this.stateDirectory, '[desktop state]');
    for (const secret of [
      credentials.apiToken,
      credentials.secretKey,
      credentials.providerApiKey,
    ]) {
      if (secret) sanitized = sanitized.replaceAll(secret, '[redacted]');
    }
    return sanitized.slice(-4_000);
  }
}
