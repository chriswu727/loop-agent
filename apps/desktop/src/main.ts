import path from 'node:path';
import { pathToFileURL } from 'node:url';
import {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  net,
  protocol,
  safeStorage,
  type IpcMainInvokeEvent,
} from 'electron';
import started from 'electron-squirrel-startup';
import {
  DESKTOP_CHANNELS,
  type DesktopProvider,
  type DesktopRuntimePhase,
  type DesktopState,
} from '@repo/desktop-contract';
import { validateGitProject } from './project.js';
import {
  DESKTOP_ORIGIN_PREFIX,
  isTrustedRendererUrl,
  LOCAL_WEB_ORIGIN,
  secureWindowOptions,
} from './security.js';
import { DesktopStore, type ProviderSecretStorage, type RuntimeCredentials } from './store.js';
import { RuntimeSupervisor } from './supervisor.js';

const DESKTOP_SCHEME = 'loop-desktop';
const allowedDesktopAssets = new Set(['onboarding.css', 'onboarding.html', 'onboarding.js']);
const smokeTest = process.argv.includes('--smoke-test');

protocol.registerSchemesAsPrivileged([
  {
    scheme: DESKTOP_SCHEME,
    privileges: { secure: true, standard: true, supportFetchAPI: true },
  },
]);
app.enableSandbox();

let mainWindow: BrowserWindow | null = null;
let store: DesktopStore;
let supervisor: RuntimeSupervisor;
let credentials: RuntimeCredentials | null = null;
let recoveryRequired = false;
let runtime: DesktopRuntimePhase = 'needs_project';
let lastError: string | null = null;
let quitting = false;
let quitAllowed = false;

if (started) {
  quitAllowed = true;
  app.quit();
}

function runtimeRoot(): string {
  if (process.env.LOOP_DESKTOP_RUNTIME_ROOT) {
    return path.resolve(process.env.LOOP_DESKTOP_RUNTIME_ROOT);
  }
  return app.isPackaged
    ? path.join(process.resourcesPath, 'runtime')
    : path.resolve(__dirname, '..', '..', '..');
}

function desktopAssetPath(asset: string): string {
  return path.join(app.getAppPath(), 'src', asset);
}

async function systemProviderSecretStorage(): Promise<ProviderSecretStorage | null> {
  try {
    if (!(await safeStorage.isAsyncEncryptionAvailable())) return null;
    if (
      process.platform === 'linux' &&
      ['basic_text', 'unknown'].includes(safeStorage.getSelectedStorageBackend())
    ) {
      return null;
    }
  } catch {
    return null;
  }
  return {
    decrypt: async (ciphertext) => {
      const decrypted = await safeStorage.decryptStringAsync(Buffer.from(ciphertext, 'base64'));
      return {
        plaintext: decrypted.result,
        replacementCiphertext: decrypted.shouldReEncrypt
          ? (await safeStorage.encryptStringAsync(decrypted.result)).toString('base64')
          : undefined,
      };
    },
    encrypt: async (plaintext) =>
      (await safeStorage.encryptStringAsync(plaintext)).toString('base64'),
  };
}

function currentState(): DesktopState {
  return {
    appVersion: app.getVersion(),
    isDesktop: true,
    lastError,
    project: store?.projectPath
      ? { name: path.basename(store.projectPath), relativePath: '.' }
      : null,
    provider: credentials?.provider ?? null,
    providerStorage: credentials?.providerStorage ?? null,
    recoveryRequired,
    runtime,
  };
}

function broadcastState(): void {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send(DESKTOP_CHANNELS.stateChanged, currentState());
  }
}

function assertTrustedSender(event: IpcMainInvokeEvent): void {
  const senderUrl = event.senderFrame?.url;
  if (!senderUrl || !isTrustedRendererUrl(senderUrl)) {
    throw new Error('Untrusted renderer cannot access desktop capabilities.');
  }
}

async function loadWebApplication(): Promise<void> {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  if (mainWindow.webContents.getURL().startsWith(LOCAL_WEB_ORIGIN)) return;
  await mainWindow.loadURL(LOCAL_WEB_ORIGIN);
}

function registerIpc(): void {
  ipcMain.handle(DESKTOP_CHANNELS.getState, (event) => {
    assertTrustedSender(event);
    return currentState();
  });
  ipcMain.handle(DESKTOP_CHANNELS.selectProject, async (event) => {
    assertTrustedSender(event);
    if (!mainWindow) return currentState();
    const selection = await dialog.showOpenDialog(mainWindow, {
      buttonLabel: 'Use this Git project',
      defaultPath: store.projectPath ?? app.getPath('home'),
      properties: ['openDirectory', 'createDirectory', 'dontAddToRecent'],
      title: 'Choose the Git project Loop may access',
    });
    const selectedPath = selection.filePaths[0];
    if (selection.canceled || !selectedPath) return currentState();

    try {
      const project = await validateGitProject(selectedPath);
      if (store.projectPath && credentials && store.projectPath !== project.absolutePath) {
        await supervisor.stop(store.projectPath, credentials).catch(() => undefined);
      }
      await store.setProjectPath(project.absolutePath);
      credentials = await store.ensureRuntimeCredentials();
      recoveryRequired = false;
      if (credentials.provider && credentials.providerApiKey) {
        await supervisor.start(project.absolutePath, credentials);
        await loadWebApplication();
      } else {
        runtime = 'stopped';
        broadcastState();
      }
    } catch (error) {
      lastError = error instanceof Error ? error.message : 'Could not select the project.';
      runtime = 'failed';
      await store.setLastError(lastError);
      broadcastState();
    }
    return currentState();
  });
  ipcMain.handle(
    DESKTOP_CHANNELS.configureProvider,
    async (event, input: { apiKey?: unknown; provider?: unknown }) => {
      assertTrustedSender(event);
      const providers: DesktopProvider[] = ['anthropic', 'deepseek', 'gemini', 'glm'];
      if (
        typeof input?.provider !== 'string' ||
        !providers.includes(input.provider as DesktopProvider) ||
        typeof input.apiKey !== 'string' ||
        input.apiKey.trim().length < 8 ||
        input.apiKey.length > 1_024
      ) {
        lastError = 'Choose a supported provider and enter a valid API key.';
        broadcastState();
        return currentState();
      }
      try {
        credentials = await store.setProvider(
          input.provider as DesktopProvider,
          input.apiKey.trim(),
        );
      } catch (error) {
        lastError =
          error instanceof Error ? error.message : 'The provider credential could not be saved.';
        await store.setLastError(lastError);
        broadcastState();
        return currentState();
      }
      lastError = null;
      await store.setLastError(null);
      if (store.projectPath) {
        try {
          await supervisor.restart(store.projectPath, credentials);
          await loadWebApplication();
        } catch {
          // The supervisor publishes its own redacted failure state.
        }
      } else {
        runtime = 'needs_project';
        broadcastState();
      }
      return currentState();
    },
  );
  ipcMain.handle(DESKTOP_CHANNELS.openSettings, async (event) => {
    assertTrustedSender(event);
    if (mainWindow) await mainWindow.loadURL(`${DESKTOP_ORIGIN_PREFIX}onboarding.html`);
    return currentState();
  });
  ipcMain.handle(DESKTOP_CHANNELS.restartRuntime, async (event) => {
    assertTrustedSender(event);
    if (!store.projectPath || !credentials?.provider || !credentials.providerApiKey) {
      return currentState();
    }
    recoveryRequired = false;
    try {
      await supervisor.restart(store.projectPath, credentials);
      await loadWebApplication();
    } catch {
      // The supervisor already publishes a redacted failure state.
    }
    return currentState();
  });
}

function createWindow(): BrowserWindow {
  const window = new BrowserWindow(secureWindowOptions(path.join(__dirname, 'preload.cjs')));
  window.webContents.setUserAgent(
    `${window.webContents.getUserAgent()} LoopDesktop/${app.getVersion()}`,
  );
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', (event, url) => {
    if (!isTrustedRendererUrl(url)) event.preventDefault();
  });
  window.webContents.on('will-redirect', (event, url) => {
    if (!isTrustedRendererUrl(url)) event.preventDefault();
  });
  window.once('ready-to-show', () => window.show());
  window.on('closed', () => {
    mainWindow = null;
  });
  return window;
}

async function initialize(): Promise<void> {
  const singleInstance = app.requestSingleInstanceLock();
  if (!singleInstance) {
    app.quit();
    return;
  }
  app.on('second-instance', () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  });

  await app.whenReady();
  app.setAppUserModelId('com.loopagent.desktop');
  store = new DesktopStore(
    path.join(app.getPath('userData'), 'runtime'),
    await systemProviderSecretStorage(),
  );
  await store.initialize();
  recoveryRequired = await store.beginSession();
  lastError = store.lastError;
  runtime = store.projectPath ? 'stopped' : 'needs_project';
  try {
    credentials = await store.ensureRuntimeCredentials();
  } catch (error) {
    credentials = null;
    lastError =
      error instanceof Error ? error.message : 'The saved provider credential could not be opened.';
    runtime = 'failed';
    await store.setLastError(lastError);
  }

  protocol.handle(DESKTOP_SCHEME, (request) => {
    const url = new URL(request.url);
    const asset = path.posix.basename(url.pathname);
    if (url.host !== 'app' || !allowedDesktopAssets.has(asset)) {
      return new Response('Not found', { status: 404 });
    }
    return net.fetch(pathToFileURL(desktopAssetPath(asset)).toString());
  });

  app.on('web-contents-created', (_event, contents) => {
    contents.session.setPermissionCheckHandler(() => false);
    contents.session.setPermissionRequestHandler((_webContents, _permission, callback) => {
      callback(false);
    });
  });

  supervisor = new RuntimeSupervisor({
    onChange: (phase, error) => {
      runtime = phase;
      lastError = error;
      void store.setLastError(error).catch(() => undefined);
      broadcastState();
      if (phase === 'ready') void loadWebApplication();
    },
    runtimeRoot: runtimeRoot(),
    stateDirectory: store.directory,
  });
  registerIpc();
  mainWindow = createWindow();
  await mainWindow.loadURL(`${DESKTOP_ORIGIN_PREFIX}onboarding.html`);

  if (smokeTest) {
    const smokeFile = process.env.LOOP_DESKTOP_SMOKE_FILE;
    const smokeProviderKey = 'loop-desktop-smoke-provider-key';
    const smokeCredentials = await store.setProvider('deepseek', smokeProviderKey);
    const { readFile, writeFile } = await import('node:fs/promises');
    const persistedCredentials = await readFile(
      path.join(store.directory, 'credentials.json'),
      'utf8',
    );
    if (persistedCredentials.includes(smokeProviderKey)) {
      throw new Error('Desktop provider key was written to disk in plaintext.');
    }
    if (smokeFile) {
      await writeFile(
        smokeFile,
        `${JSON.stringify({
          credentialStorage: smokeCredentials.providerStorage,
          sandbox: true,
          status: 'passed',
        })}\n`,
        { mode: 0o600 },
      );
    }
    quitAllowed = true;
    await store.markCleanShutdown();
    app.quit();
    return;
  }

  if (store.projectPath && credentials?.provider && credentials.providerApiKey) {
    void supervisor.start(store.projectPath, credentials).catch(() => undefined);
  }
}

app.on('activate', () => {
  if (!mainWindow) {
    mainWindow = createWindow();
    void mainWindow.loadURL(
      runtime === 'ready' ? LOCAL_WEB_ORIGIN : `${DESKTOP_ORIGIN_PREFIX}onboarding.html`,
    );
  }
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', (event) => {
  if (quitAllowed) return;
  if (!supervisor || !store) {
    quitAllowed = true;
    return;
  }
  event.preventDefault();
  if (quitting) return;
  quitting = true;
  void supervisor
    .stop(store.projectPath, credentials)
    .catch(() => undefined)
    .then(() => store.markCleanShutdown())
    .finally(() => {
      quitAllowed = true;
      app.quit();
    });
});

if (!started) {
  void initialize().catch((error: unknown) => {
    const message = error instanceof Error ? error.message : 'Loop Desktop could not start.';
    dialog.showErrorBox('Loop Desktop failed to start', message);
    app.exit(1);
  });
}
