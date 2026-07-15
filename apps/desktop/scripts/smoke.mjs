import { spawn } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import electron from 'electron';

const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const temporaryDirectory = await mkdtemp(path.join(os.tmpdir(), 'loop-desktop-smoke-'));
const smokeFile = path.join(temporaryDirectory, 'result.json');

try {
  await new Promise((resolve, reject) => {
    const child = spawn(
      electron,
      [desktopRoot, '--smoke-test', `--user-data-dir=${path.join(temporaryDirectory, 'profile')}`],
      {
        env: { ...process.env, LOOP_DESKTOP_SMOKE_FILE: smokeFile },
        stdio: 'inherit',
      },
    );
    const timer = setTimeout(() => {
      child.kill('SIGTERM');
      reject(new Error('Loop Desktop startup smoke timed out.'));
    }, 30_000);
    child.once('error', reject);
    child.once('exit', (code) => {
      clearTimeout(timer);
      if (code === 0) resolve();
      else reject(new Error(`Loop Desktop startup smoke exited with ${code}.`));
    });
  });
  const result = JSON.parse(await readFile(smokeFile, 'utf8'));
  if (
    result.status !== 'passed' ||
    result.sandbox !== true ||
    !['encrypted', 'memory'].includes(result.credentialStorage)
  ) {
    throw new Error('Loop Desktop did not report a sandboxed startup and protected credentials.');
  }
  process.stdout.write(`${JSON.stringify(result)}\n`);
} finally {
  await rm(temporaryDirectory, { force: true, recursive: true });
}
