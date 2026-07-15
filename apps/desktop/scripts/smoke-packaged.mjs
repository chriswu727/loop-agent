import { spawn } from 'node:child_process';
import { mkdtemp, readdir, readFile, realpath, rm, stat } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { FuseV1Options, getCurrentFuseWire } from '@electron/fuses';

const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const outputRoot = path.join(desktopRoot, 'out');

async function findExecutable(directory) {
  for (const entry of await readdir(directory)) {
    const candidate = path.join(directory, entry);
    const details = await stat(candidate);
    if (details.isDirectory()) {
      const nested = await findExecutable(candidate);
      if (nested) return nested;
      continue;
    }
    const normalized = candidate.replaceAll('\\', '/');
    if (
      (process.platform === 'darwin' && normalized.endsWith('.app/Contents/MacOS/loop-desktop')) ||
      (process.platform === 'win32' && normalized.endsWith('/loop-desktop.exe')) ||
      (process.platform === 'linux' && normalized.endsWith('/loop-desktop'))
    ) {
      return candidate;
    }
  }
  return null;
}

const configuredExecutable = process.env.LOOP_DESKTOP_EXECUTABLE;
const executable = configuredExecutable
  ? await realpath(path.resolve(configuredExecutable))
  : await findExecutable(outputRoot);
if (!executable) throw new Error('Could not find the packaged Loop Desktop executable.');
const fuses = await getCurrentFuseWire(executable);
const expectedFuses = new Map([
  [FuseV1Options.RunAsNode, 48],
  [FuseV1Options.EnableCookieEncryption, 49],
  [FuseV1Options.EnableNodeOptionsEnvironmentVariable, 48],
  [FuseV1Options.EnableNodeCliInspectArguments, 48],
  [FuseV1Options.EnableEmbeddedAsarIntegrityValidation, 49],
  [FuseV1Options.OnlyLoadAppFromAsar, 49],
  [FuseV1Options.LoadBrowserProcessSpecificV8Snapshot, 48],
  [FuseV1Options.GrantFileProtocolExtraPrivileges, 48],
  [FuseV1Options.WasmTrapHandlers, 49],
]);
for (const [fuse, expected] of expectedFuses) {
  if (fuses[fuse] !== expected) throw new Error(`Packaged Electron fuse ${fuse} is not hardened.`);
}
const temporaryDirectory = await mkdtemp(path.join(os.tmpdir(), 'loop-desktop-packaged-smoke-'));
const smokeFile = path.join(temporaryDirectory, 'result.json');

try {
  await new Promise((resolve, reject) => {
    const child = spawn(
      executable,
      ['--smoke-test', `--user-data-dir=${path.join(temporaryDirectory, 'profile')}`],
      {
        env: { ...process.env, LOOP_DESKTOP_SMOKE_FILE: smokeFile },
        stdio: 'inherit',
      },
    );
    const timer = setTimeout(() => {
      child.kill('SIGTERM');
      reject(new Error('Packaged Loop Desktop startup smoke timed out.'));
    }, 30_000);
    child.once('error', reject);
    child.once('exit', (code) => {
      clearTimeout(timer);
      if (code === 0) resolve();
      else reject(new Error(`Packaged Loop Desktop startup smoke exited with ${code}.`));
    });
  });
  const result = JSON.parse(await readFile(smokeFile, 'utf8'));
  if (
    result.status !== 'passed' ||
    result.sandbox !== true ||
    !['encrypted', 'memory'].includes(result.credentialStorage)
  ) {
    throw new Error(
      'Packaged Loop Desktop did not report a sandboxed startup and protected credentials.',
    );
  }
  process.stdout.write(`${JSON.stringify({ ...result, fuses: 'hardened', packaged: true })}\n`);
} finally {
  await rm(temporaryDirectory, { force: true, recursive: true });
}
