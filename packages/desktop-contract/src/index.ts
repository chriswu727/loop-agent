export const DESKTOP_CHANNELS = {
  getState: 'loop-desktop:get-state',
  selectProject: 'loop-desktop:select-project',
  restartRuntime: 'loop-desktop:restart-runtime',
  configureProvider: 'loop-desktop:configure-provider',
  openSettings: 'loop-desktop:open-settings',
  stateChanged: 'loop-desktop:state-changed',
} as const;

export type DesktopRuntimePhase =
  'needs_project' | 'stopped' | 'starting' | 'ready' | 'stopping' | 'failed';

export type DesktopProvider = 'anthropic' | 'deepseek' | 'gemini' | 'glm';
export type DesktopProviderStorage = 'encrypted' | 'memory';

export interface DesktopProject {
  name: string;
  relativePath: '.';
}

export interface DesktopState {
  appVersion: string;
  isDesktop: true;
  lastError: string | null;
  project: DesktopProject | null;
  provider: DesktopProvider | null;
  providerStorage: DesktopProviderStorage | null;
  recoveryRequired: boolean;
  runtime: DesktopRuntimePhase;
}

export interface LoopDesktopApi {
  configureProvider(provider: DesktopProvider, apiKey: string): Promise<DesktopState>;
  getCachedState(): DesktopState | null;
  getState(): Promise<DesktopState>;
  onStateChange(listener: () => void): () => void;
  openSettings(): Promise<DesktopState>;
  restartRuntime(): Promise<DesktopState>;
  selectProject(): Promise<DesktopState>;
}
