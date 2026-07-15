import { contextBridge, ipcRenderer } from 'electron';
import { DESKTOP_CHANNELS, type DesktopState, type LoopDesktopApi } from '@repo/desktop-contract';

let cachedState: DesktopState | null = null;
const listeners = new Set<() => void>();

function updateState(state: DesktopState): DesktopState {
  const cloned = structuredClone(state);
  if (cloned.project) Object.freeze(cloned.project);
  cachedState = Object.freeze(cloned);
  for (const listener of listeners) listener();
  return cachedState;
}

ipcRenderer.on(DESKTOP_CHANNELS.stateChanged, (_event, state: DesktopState) => {
  updateState(state);
});

const api: LoopDesktopApi = {
  configureProvider: async (provider, apiKey) =>
    updateState(await ipcRenderer.invoke(DESKTOP_CHANNELS.configureProvider, { apiKey, provider })),
  getCachedState: () => cachedState,
  getState: async () => updateState(await ipcRenderer.invoke(DESKTOP_CHANNELS.getState)),
  onStateChange: (listener) => {
    if (typeof listener !== 'function') return () => undefined;
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  openSettings: async () => updateState(await ipcRenderer.invoke(DESKTOP_CHANNELS.openSettings)),
  restartRuntime: async () =>
    updateState(await ipcRenderer.invoke(DESKTOP_CHANNELS.restartRuntime)),
  selectProject: async () => updateState(await ipcRenderer.invoke(DESKTOP_CHANNELS.selectProject)),
};

contextBridge.exposeInMainWorld('loopDesktop', Object.freeze(api));
void api.getState().catch(() => undefined);
