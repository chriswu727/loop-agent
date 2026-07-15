import { access, mkdtemp, readFile, rm, stat } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { DesktopStore, type ProviderSecretStorage } from '../src/store.js';

const temporaryDirectories: string[] = [];
const secretStorage: ProviderSecretStorage = {
  decrypt: async (ciphertext) => ({
    plaintext: Buffer.from(ciphertext, 'base64url')
      .toString('utf8')
      .replace(/^encrypted:/u, ''),
  }),
  encrypt: async (plaintext) => Buffer.from(`encrypted:${plaintext}`).toString('base64url'),
};

async function temporaryDirectory(): Promise<string> {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'loop-desktop-store-'));
  temporaryDirectories.push(directory);
  return directory;
}

afterEach(async () => {
  await Promise.all(
    temporaryDirectories
      .splice(0)
      .map((directory) => rm(directory, { force: true, recursive: true })),
  );
});

describe('desktop recovery store', () => {
  it('detects an interrupted session and clears it after a clean shutdown', async () => {
    const directory = await temporaryDirectory();
    const first = new DesktopStore(directory);
    await first.initialize();
    expect(await first.beginSession()).toBe(false);

    const afterCrash = new DesktopStore(directory);
    await afterCrash.initialize();
    expect(await afterCrash.beginSession()).toBe(true);
    await afterCrash.markCleanShutdown();

    const afterCleanExit = new DesktopStore(directory);
    await afterCleanExit.initialize();
    expect(await afterCleanExit.beginSession()).toBe(false);
  });

  it('creates stable private credentials and signing keys', async () => {
    const directory = await temporaryDirectory();
    const store = new DesktopStore(directory, secretStorage);
    await store.initialize();
    const first = await store.ensureRuntimeCredentials();
    const second = await store.ensureRuntimeCredentials();

    expect(second).toEqual(first);
    expect(first.apiToken.length).toBeGreaterThan(32);
    expect(await readFile(path.join(directory, 'authority-private.pem'), 'utf8')).toContain(
      'PRIVATE KEY',
    );
    expect(await readFile(path.join(directory, 'authority-public.pem'), 'utf8')).toContain(
      'PUBLIC KEY',
    );
    expect(await readFile(path.join(directory, 'receipt-private.pem'), 'utf8')).toContain(
      'PRIVATE KEY',
    );
    await access(path.join(directory, 'credentials.json'));
    if (process.platform !== 'win32') {
      expect((await stat(path.join(directory, 'credentials.json'))).mode & 0o777).toBe(0o600);
      expect((await stat(path.join(directory, 'authority-private.pem'))).mode & 0o777).toBe(0o600);
      expect((await stat(path.join(directory, 'authority-public.pem'))).mode & 0o777).toBe(0o644);
    }

    const configured = await store.setProvider('anthropic', 'provider-secret');
    expect(configured).toMatchObject({
      provider: 'anthropic',
      providerApiKey: 'provider-secret',
      providerStorage: 'encrypted',
    });
    expect(await store.ensureRuntimeCredentials()).toEqual(configured);
    expect(await readFile(path.join(directory, 'credentials.json'), 'utf8')).not.toContain(
      'provider-secret',
    );
  });

  it('serializes concurrent credential generation', async () => {
    const directory = await temporaryDirectory();
    const store = new DesktopStore(directory);
    await store.initialize();

    const [first, second, third] = await Promise.all([
      store.ensureRuntimeCredentials(),
      store.ensureRuntimeCredentials(),
      store.ensureRuntimeCredentials(),
    ]);

    expect(second).toEqual(first);
    expect(third).toEqual(first);
    expect(JSON.parse(await readFile(path.join(directory, 'credentials.json'), 'utf8'))).toEqual(
      expect.objectContaining({
        apiToken: first.apiToken,
        provider: null,
        providerApiKeyCiphertext: null,
        secretKey: first.secretKey,
        version: 1,
      }),
    );
  });

  it('keeps a provider key in memory when secure operating-system storage is unavailable', async () => {
    const directory = await temporaryDirectory();
    const store = new DesktopStore(directory);
    await store.initialize();

    const configured = await store.setProvider('anthropic', 'provider-secret');

    expect(configured).toMatchObject({
      provider: 'anthropic',
      providerApiKey: 'provider-secret',
      providerStorage: 'memory',
    });
    expect(await store.ensureRuntimeCredentials()).toEqual(configured);
    const persisted = await readFile(path.join(directory, 'credentials.json'), 'utf8');
    expect(persisted).not.toContain('provider-secret');
    expect(JSON.parse(persisted)).toMatchObject({
      provider: null,
      providerApiKeyCiphertext: null,
    });
  });

  it('allows an inaccessible encrypted provider key to be replaced for the session', async () => {
    const directory = await temporaryDirectory();
    const encryptedStore = new DesktopStore(directory, secretStorage);
    await encryptedStore.initialize();
    await encryptedStore.setProvider('anthropic', 'old-provider-secret');

    const memoryOnlyStore = new DesktopStore(directory);
    await memoryOnlyStore.initialize();
    await expect(memoryOnlyStore.ensureRuntimeCredentials()).rejects.toThrow(
      'saved provider key cannot be opened',
    );

    const replacement = await memoryOnlyStore.setProvider('gemini', 'new-provider-secret');
    expect(replacement).toMatchObject({
      provider: 'gemini',
      providerApiKey: 'new-provider-secret',
      providerStorage: 'memory',
    });
    const persisted = await readFile(path.join(directory, 'credentials.json'), 'utf8');
    expect(persisted).not.toContain('old-provider-secret');
    expect(persisted).not.toContain('new-provider-secret');
    expect(JSON.parse(persisted)).toMatchObject({ provider: null });
  });

  it('falls back to memory if the operating system rejects encryption', async () => {
    const directory = await temporaryDirectory();
    const unavailableStorage: ProviderSecretStorage = {
      decrypt: secretStorage.decrypt,
      encrypt: async () => {
        throw new Error('keychain unavailable');
      },
    };
    const store = new DesktopStore(directory, unavailableStorage);
    await store.initialize();

    const configured = await store.setProvider('anthropic', 'provider-secret');

    expect(configured).toMatchObject({
      provider: 'anthropic',
      providerApiKey: 'provider-secret',
      providerStorage: 'memory',
    });
    const persisted = await readFile(path.join(directory, 'credentials.json'), 'utf8');
    expect(persisted).not.toContain('provider-secret');
    expect(JSON.parse(persisted)).toMatchObject({ provider: null });
  });
});
