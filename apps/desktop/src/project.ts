import { execFile } from 'node:child_process';
import { realpath, stat } from 'node:fs/promises';
import path from 'node:path';
import { promisify } from 'node:util';

const execFileAsync = promisify(execFile);

export interface ValidatedProject {
  absolutePath: string;
  name: string;
}

function comparable(projectPath: string): string {
  const normalized = path.normalize(projectPath);
  return process.platform === 'win32' ? normalized.toLowerCase() : normalized;
}

export async function validateGitProject(selectedPath: string): Promise<ValidatedProject> {
  const absolutePath = await realpath(selectedPath);
  if (!(await stat(absolutePath)).isDirectory()) throw new Error('Select a project directory.');

  let topLevel: string;
  let statusOutput: string;
  try {
    const commonOptions = { encoding: 'utf8' as const, maxBuffer: 1024 * 1024, timeout: 10_000 };
    const root = await execFileAsync(
      'git',
      ['-c', 'safe.directory=*', '-C', absolutePath, 'rev-parse', '--show-toplevel'],
      commonOptions,
    );
    topLevel = await realpath(root.stdout.trim());
    const statusResult = await execFileAsync(
      'git',
      ['-c', 'safe.directory=*', '-C', absolutePath, 'status', '--porcelain=v1'],
      commonOptions,
    );
    statusOutput = statusResult.stdout;
  } catch {
    throw new Error('The selected directory is not an accessible Git repository.');
  }

  if (comparable(topLevel) !== comparable(absolutePath)) {
    throw new Error('Select the Git repository root, not a folder inside it.');
  }
  if (statusOutput.trim()) {
    throw new Error('The selected Git repository must be clean before Loop can isolate it.');
  }
  return { absolutePath, name: path.basename(absolutePath) };
}
