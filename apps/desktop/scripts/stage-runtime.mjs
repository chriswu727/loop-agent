import { cp, mkdir, rm, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repositoryRoot = path.resolve(desktopRoot, '..', '..');
const outputRoot = path.join(desktopRoot, 'runtime');

const entries = [
  '.dockerignore',
  '.env.example',
  '.npmrc',
  'docker-compose.yml',
  'package.json',
  'pnpm-lock.yaml',
  'pnpm-workspace.yaml',
  'turbo.json',
  'apps/api/README.md',
  'apps/api/alembic',
  'apps/api/alembic.ini',
  'apps/api/app',
  'apps/api/pyproject.toml',
  'apps/api/requirements.lock',
  'apps/api/sandbox-requirements.lock',
  'apps/api/sandbox.Dockerfile',
  'apps/api/skills',
  'apps/provider-gateway-runtime/package-lock.json',
  'apps/provider-gateway-runtime/package.json',
  'apps/web/app',
  'apps/web/components',
  'apps/web/eslint.config.mjs',
  'apps/web/lib',
  'apps/web/next.config.mjs',
  'apps/web/package.json',
  'apps/web/postcss.config.mjs',
  'apps/web/tsconfig.json',
  'infra/desktop',
  'infra/docker',
  'packages/api-contract',
  'packages/desktop-contract',
  'packages/eslint-config',
  'packages/tsconfig',
];

await rm(outputRoot, { force: true, recursive: true });
await mkdir(outputRoot, { recursive: true });
for (const entry of entries) {
  const source = path.join(repositoryRoot, entry);
  try {
    await stat(source);
  } catch {
    continue;
  }
  const destination = path.join(outputRoot, entry);
  await mkdir(path.dirname(destination), { recursive: true });
  await cp(source, destination, {
    filter: (candidate) =>
      !candidate.includes(`${path.sep}node_modules${path.sep}`) &&
      !candidate.includes(`${path.sep}.next${path.sep}`) &&
      !candidate.includes(`${path.sep}__pycache__${path.sep}`) &&
      !candidate.includes(`${path.sep}.turbo${path.sep}`) &&
      !candidate.endsWith('.test.ts') &&
      !candidate.endsWith('.test.tsx'),
    recursive: true,
  });
}
