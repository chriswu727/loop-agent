import { generateKeyPairSync, randomBytes } from 'node:crypto';
import { chmod, mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import path from 'node:path';
import type { DesktopProvider, DesktopProviderStorage } from '@repo/desktop-contract';

interface PersistedState {
  cleanShutdown: boolean;
  lastError: string | null;
  lastStartedAt: string | null;
  projectPath: string | null;
  version: 1;
}

interface PersistedRuntimeCredentials {
  apiToken: string;
  provider: DesktopProvider | null;
  providerApiKeyCiphertext: string | null;
  secretKey: string;
  version: 1;
}

export interface ProviderSecretStorage {
  decrypt(ciphertext: string): Promise<{
    plaintext: string;
    replacementCiphertext?: string;
  }>;
  encrypt(plaintext: string): Promise<string>;
}

export interface RuntimeCredentials {
  apiToken: string;
  provider: DesktopProvider | null;
  providerApiKey: string | null;
  providerStorage: DesktopProviderStorage | null;
  secretKey: string;
}

const DEFAULT_STATE: PersistedState = {
  cleanShutdown: true,
  lastError: null,
  lastStartedAt: null,
  projectPath: null,
  version: 1,
};

const SUPPORTED_PROVIDERS: DesktopProvider[] = ['anthropic', 'deepseek', 'gemini', 'glm'];

function isPersistedState(value: unknown): value is PersistedState {
  if (!value || typeof value !== 'object') return false;
  const state = value as Record<string, unknown>;
  return (
    state.version === 1 &&
    typeof state.cleanShutdown === 'boolean' &&
    (typeof state.lastError === 'string' || state.lastError === null) &&
    (typeof state.lastStartedAt === 'string' || state.lastStartedAt === null) &&
    (typeof state.projectPath === 'string' || state.projectPath === null)
  );
}

function isPersistedRuntimeCredentials(value: unknown): value is PersistedRuntimeCredentials {
  if (!value || typeof value !== 'object') return false;
  const credentials = value as Record<string, unknown>;
  const provider = credentials.provider;
  const ciphertext = credentials.providerApiKeyCiphertext;
  return (
    credentials.version === 1 &&
    typeof credentials.apiToken === 'string' &&
    credentials.apiToken.length > 32 &&
    typeof credentials.secretKey === 'string' &&
    credentials.secretKey.length > 32 &&
    ((provider === null && ciphertext === null) ||
      (typeof provider === 'string' &&
        SUPPORTED_PROVIDERS.includes(provider as DesktopProvider) &&
        typeof ciphertext === 'string' &&
        ciphertext.length > 0))
  );
}

async function atomicWrite(filePath: string, contents: string): Promise<void> {
  const temporaryPath = `${filePath}.${process.pid}-${randomBytes(6).toString('hex')}.tmp`;
  await writeFile(temporaryPath, contents, { encoding: 'utf8', mode: 0o600 });
  await rename(temporaryPath, filePath);
  await chmod(filePath, 0o600);
}

export class DesktopStore {
  readonly directory: string;
  private readonly statePath: string;
  private readonly providerSecretStorage: ProviderSecretStorage | null;
  private credentialQueue: Promise<void> = Promise.resolve();
  private runtimeCredentials: RuntimeCredentials | null = null;
  private saveQueue: Promise<void> = Promise.resolve();
  private state: PersistedState = { ...DEFAULT_STATE };

  constructor(directory: string, providerSecretStorage: ProviderSecretStorage | null = null) {
    this.directory = directory;
    this.providerSecretStorage = providerSecretStorage;
    this.statePath = path.join(directory, 'desktop-state.json');
  }

  async initialize(): Promise<void> {
    await mkdir(this.directory, { recursive: true, mode: 0o700 });
    await chmod(this.directory, 0o700);
    try {
      const parsed: unknown = JSON.parse(await readFile(this.statePath, 'utf8'));
      if (!isPersistedState(parsed)) throw new Error('invalid desktop state shape');
      this.state = parsed;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') {
        const corruptPath = path.join(this.directory, `desktop-state.corrupt-${Date.now()}.json`);
        await rename(this.statePath, corruptPath).catch(() => undefined);
      }
      this.state = { ...DEFAULT_STATE };
      await this.save();
    }
  }

  get projectPath(): string | null {
    return this.state.projectPath;
  }

  get lastError(): string | null {
    return this.state.lastError;
  }

  async beginSession(): Promise<boolean> {
    const recoveryRequired = !this.state.cleanShutdown && this.state.lastStartedAt !== null;
    this.state.cleanShutdown = false;
    this.state.lastStartedAt = new Date().toISOString();
    await this.save();
    return recoveryRequired;
  }

  async markCleanShutdown(): Promise<void> {
    this.state.cleanShutdown = true;
    await this.save();
  }

  async setProjectPath(projectPath: string): Promise<void> {
    this.state.projectPath = projectPath;
    this.state.lastError = null;
    await this.save();
  }

  async setLastError(message: string | null): Promise<void> {
    this.state.lastError = message;
    await this.save();
  }

  async ensureRuntimeCredentials(): Promise<RuntimeCredentials> {
    return this.enqueueCredentials(async () => this.ensureRuntimeCredentialsInternal());
  }

  async setProvider(provider: DesktopProvider, apiKey: string): Promise<RuntimeCredentials> {
    return this.enqueueCredentials(async () => {
      const credentials = await this.ensureRuntimeCredentialsInternal(true);
      let updated: RuntimeCredentials = {
        ...credentials,
        provider,
        providerApiKey: apiKey,
        providerStorage: this.providerSecretStorage ? 'encrypted' : 'memory',
      };
      if (!this.providerSecretStorage) {
        await this.writeCredentials(
          {
            ...credentials,
            provider: null,
            providerApiKey: null,
            providerStorage: null,
          },
          null,
        );
        this.runtimeCredentials = updated;
        return updated;
      }
      let providerApiKeyCiphertext: string;
      try {
        providerApiKeyCiphertext = await this.providerSecretStorage.encrypt(apiKey);
      } catch {
        updated = { ...updated, providerStorage: 'memory' };
        await this.writeCredentials(
          {
            ...credentials,
            provider: null,
            providerApiKey: null,
            providerStorage: null,
          },
          null,
        );
        this.runtimeCredentials = updated;
        return updated;
      }
      await this.writeCredentials(updated, providerApiKeyCiphertext);
      this.runtimeCredentials = updated;
      return updated;
    });
  }

  private async ensureRuntimeCredentialsInternal(
    replaceInaccessibleProvider = false,
  ): Promise<RuntimeCredentials> {
    if (this.runtimeCredentials) return this.runtimeCredentials;
    const credentialsPath = path.join(this.directory, 'credentials.json');
    let credentials: RuntimeCredentials | null = null;
    let persisted: PersistedRuntimeCredentials | null = null;
    try {
      const parsed: unknown = JSON.parse(await readFile(credentialsPath, 'utf8'));
      if (isPersistedRuntimeCredentials(parsed)) persisted = parsed;
    } catch {
      persisted = null;
    }
    if (persisted) {
      let provider = persisted.provider;
      let providerApiKey: string | null = null;
      if (persisted.provider && persisted.providerApiKeyCiphertext) {
        if (!this.providerSecretStorage) {
          if (!replaceInaccessibleProvider) {
            throw new Error(
              'The saved provider key cannot be opened because the operating system secure credential store is unavailable.',
            );
          }
          provider = null;
        } else {
          let decrypted: Awaited<ReturnType<ProviderSecretStorage['decrypt']>> | null = null;
          try {
            decrypted = await this.providerSecretStorage.decrypt(
              persisted.providerApiKeyCiphertext,
            );
          } catch {
            if (!replaceInaccessibleProvider) {
              throw new Error(
                'The operating system secure credential store could not decrypt the saved provider key.',
              );
            }
            provider = null;
          }
          if (decrypted) {
            if (decrypted.plaintext.length < 8) {
              if (!replaceInaccessibleProvider) {
                throw new Error(
                  'The saved provider credential is invalid. Configure the provider again.',
                );
              }
              provider = null;
            } else {
              providerApiKey = decrypted.plaintext;
              if (decrypted.replacementCiphertext) {
                persisted.providerApiKeyCiphertext = decrypted.replacementCiphertext;
                await atomicWrite(credentialsPath, `${JSON.stringify(persisted)}\n`);
              }
            }
          }
        }
      }
      credentials = {
        apiToken: persisted.apiToken,
        provider,
        providerApiKey,
        providerStorage: provider ? 'encrypted' : null,
        secretKey: persisted.secretKey,
      };
    }
    if (!credentials) {
      credentials = {
        apiToken: randomBytes(32).toString('base64url'),
        provider: null,
        providerApiKey: null,
        providerStorage: null,
        secretKey: randomBytes(48).toString('base64url'),
      };
      await this.writeCredentials(credentials, null);
    }

    const authorityPrivatePath = path.join(this.directory, 'authority-private.pem');
    const authorityPublicPath = path.join(this.directory, 'authority-public.pem');
    const receiptPrivatePath = path.join(this.directory, 'receipt-private.pem');
    try {
      await readFile(authorityPrivatePath, 'utf8');
      await readFile(authorityPublicPath, 'utf8');
    } catch {
      const { privateKey, publicKey } = generateKeyPairSync('ed25519');
      await atomicWrite(
        authorityPrivatePath,
        privateKey.export({ format: 'pem', type: 'pkcs8' }).toString(),
      );
      await atomicWrite(
        authorityPublicPath,
        publicKey.export({ format: 'pem', type: 'spki' }).toString(),
      );
      await chmod(authorityPublicPath, 0o644);
    }
    try {
      await readFile(receiptPrivatePath, 'utf8');
    } catch {
      const { privateKey } = generateKeyPairSync('ed25519');
      await atomicWrite(
        receiptPrivatePath,
        privateKey.export({ format: 'pem', type: 'pkcs8' }).toString(),
      );
    }
    this.runtimeCredentials = credentials;
    return credentials;
  }

  private async writeCredentials(
    credentials: RuntimeCredentials,
    providerApiKeyCiphertext: string | null,
  ): Promise<void> {
    const persisted: PersistedRuntimeCredentials = {
      apiToken: credentials.apiToken,
      provider: credentials.provider,
      providerApiKeyCiphertext,
      secretKey: credentials.secretKey,
      version: 1,
    };
    await atomicWrite(
      path.join(this.directory, 'credentials.json'),
      `${JSON.stringify(persisted)}\n`,
    );
  }

  private enqueueCredentials<T>(action: () => Promise<T>): Promise<T> {
    const result = this.credentialQueue.then(action, action);
    this.credentialQueue = result.then(
      () => undefined,
      () => undefined,
    );
    return result;
  }

  private save(): Promise<void> {
    const contents = `${JSON.stringify(this.state, null, 2)}\n`;
    const result = this.saveQueue.then(
      () => atomicWrite(this.statePath, contents),
      () => atomicWrite(this.statePath, contents),
    );
    this.saveQueue = result.catch(() => undefined);
    return result;
  }
}
