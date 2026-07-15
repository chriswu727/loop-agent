import { execFile } from 'node:child_process';
import { mkdir, mkdtemp, realpath, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { promisify } from 'node:util';
import { afterEach, describe, expect, it } from 'vitest';
import { validateGitProject } from '../src/project.js';

const execFileAsync = promisify(execFile);
const temporaryDirectories: string[] = [];

async function cleanRepository(): Promise<string> {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'loop-desktop-project-'));
  temporaryDirectories.push(directory);
  await execFileAsync('git', ['init', '--quiet', directory]);
  await execFileAsync('git', ['-C', directory, 'config', 'user.email', 'loop@example.com']);
  await execFileAsync('git', ['-C', directory, 'config', 'user.name', 'Loop Test']);
  await writeFile(path.join(directory, 'README.md'), 'verified project\n');
  await execFileAsync('git', ['-C', directory, 'add', 'README.md']);
  await execFileAsync('git', ['-C', directory, 'commit', '--quiet', '-m', 'Initial']);
  return directory;
}

afterEach(async () => {
  await Promise.all(
    temporaryDirectories
      .splice(0)
      .map((directory) => rm(directory, { force: true, recursive: true })),
  );
});

describe('native project selection', () => {
  it('accepts an exact, clean Git repository root', async () => {
    const repository = await cleanRepository();
    await expect(validateGitProject(repository)).resolves.toEqual({
      absolutePath: await realpath(repository),
      name: path.basename(repository),
    });
  });

  it('rejects dirty projects and nested directories', async () => {
    const dirtyRepository = await cleanRepository();
    await writeFile(path.join(dirtyRepository, 'README.md'), 'dirty\n');
    await expect(validateGitProject(dirtyRepository)).rejects.toThrow('must be clean');

    const nestedRepository = await cleanRepository();
    const nested = path.join(nestedRepository, 'src');
    await mkdir(nested);
    await expect(validateGitProject(nested)).rejects.toThrow('repository root');
  });
});
