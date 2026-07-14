// @vitest-environment node

import { NextRequest } from 'next/server';
import { afterEach, describe, expect, it, vi } from 'vitest';

const requiredAuthEnv = {
  APP_BASE_URL: 'https://loop.example.com',
  GITHUB_CLIENT_ID: 'client-id',
  GITHUB_CLIENT_SECRET: 'client-secret',
  LOOP_SESSION_SECRET: 'test-session-secret-that-is-at-least-32-bytes',
  WEB_AUTH_REQUIRED: 'true',
};

afterEach(() => {
  for (const key of Object.keys(requiredAuthEnv)) delete process.env[key];
  delete process.env.LOOP_API_TOKEN;
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe('same-origin API proxy', () => {
  it('fails closed when production web auth has no user session', async () => {
    Object.assign(process.env, requiredAuthEnv);
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const { GET } = await import('@/app/api/loop/[...path]/route');
    const response = await GET(new NextRequest('https://loop.example.com/api/loop/api/v1/tasks'), {
      params: Promise.resolve({ path: ['api', 'v1', 'tasks'] }),
    });

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('forwards the HTTP-only user session instead of the service token', async () => {
    Object.assign(process.env, requiredAuthEnv);
    process.env.LOOP_API_TOKEN = 'service-token-that-must-not-be-forwarded';
    const { createLoopSession } = await import('@/lib/session');
    const session = await createLoopSession({ id: 42, login: 'octocat' });
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ items: [] }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const { GET } = await import('@/app/api/loop/[...path]/route');
    const request = new NextRequest('https://loop.example.com/api/loop/api/v1/tasks', {
      headers: { cookie: `loop_session=${session}` },
    });

    const response = await GET(request, {
      params: Promise.resolve({ path: ['api', 'v1', 'tasks'] }),
    });

    expect(response.status).toBe(200);
    const upstream = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(new Headers(upstream.headers).get('authorization')).toBe(`Bearer ${session}`);
    expect(new Headers(upstream.headers).has('cookie')).toBe(false);
  });
});
