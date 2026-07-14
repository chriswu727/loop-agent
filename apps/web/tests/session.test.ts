// @vitest-environment node

import { afterEach, describe, expect, it, vi } from 'vitest';

const secret = 'test-session-secret-that-is-at-least-32-bytes';

afterEach(() => {
  delete process.env.LOOP_SESSION_SECRET;
  vi.resetModules();
});

describe('Loop session', () => {
  it('round-trips a scoped, short-lived GitHub identity', async () => {
    process.env.LOOP_SESSION_SECRET = secret;
    const { createLoopSession, verifyLoopSession } = await import('@/lib/session');

    const token = await createLoopSession({ id: 42, login: 'octocat', name: 'Mona' });
    const session = await verifyLoopSession(token);

    expect(session.sub).toBe('github:42');
    expect(session.login).toBe('octocat');
    expect(session.iss).toBe('loop-web');
    expect(session.aud).toBe('loop-api');
  });

  it('rejects a modified session token', async () => {
    process.env.LOOP_SESSION_SECRET = secret;
    const { createLoopSession, verifyLoopSession } = await import('@/lib/session');
    const token = await createLoopSession({ id: 42, login: 'octocat' });
    const parts = token.split('.');
    parts[2] = `${parts[2]?.startsWith('a') ? 'b' : 'a'}${parts[2]?.slice(1)}`;
    const forged = parts.join('.');

    await expect(verifyLoopSession(forged)).rejects.toThrow();
  });
});
