import { timingSafeEqual } from 'node:crypto';
import { NextResponse, type NextRequest } from 'next/server';
import { z } from 'zod';
import { env } from '@/lib/env';
import {
  createLoopSession,
  OAUTH_STATE_COOKIE,
  OAUTH_VERIFIER_COOKIE,
  secureCookieOptions,
  SESSION_COOKIE,
  SESSION_MAX_AGE_SECONDS,
} from '@/lib/session';

export const runtime = 'nodejs';

const tokenSchema = z.object({ access_token: z.string().min(1), token_type: z.string() });
const userSchema = z.object({
  id: z.number().int().positive(),
  login: z.string().min(1),
  name: z.string().nullable().optional(),
  avatar_url: z.string().url().nullable().optional(),
});

function equal(left: string, right: string): boolean {
  const a = Buffer.from(left);
  const b = Buffer.from(right);
  return a.length === b.length && timingSafeEqual(a, b);
}

function failed(request: NextRequest, reason: string): NextResponse {
  const target = new URL('/', env.APP_BASE_URL ?? request.nextUrl.origin);
  target.searchParams.set('auth_error', reason);
  const response = NextResponse.redirect(target);
  clearOauthCookies(response);
  return response;
}

function clearOauthCookies(response: NextResponse): void {
  const options = { ...secureCookieOptions(0), path: '/api/auth/github' };
  response.cookies.set(OAUTH_STATE_COOKIE, '', options);
  response.cookies.set(OAUTH_VERIFIER_COOKIE, '', options);
}

export async function GET(request: NextRequest) {
  const code = request.nextUrl.searchParams.get('code') ?? '';
  const state = request.nextUrl.searchParams.get('state') ?? '';
  const expectedState = request.cookies.get(OAUTH_STATE_COOKIE)?.value ?? '';
  const verifier = request.cookies.get(OAUTH_VERIFIER_COOKIE)?.value ?? '';
  if (!code || !state || !expectedState || !verifier || !equal(state, expectedState)) {
    return failed(request, 'invalid_oauth_state');
  }
  if (!env.GITHUB_CLIENT_ID || !env.GITHUB_CLIENT_SECRET || !env.LOOP_SESSION_SECRET) {
    return failed(request, 'auth_not_configured');
  }

  try {
    const callback = new URL(
      '/api/auth/github/callback',
      env.APP_BASE_URL ?? request.nextUrl.origin,
    );
    const tokenResponse = await fetch('https://github.com/login/oauth/access_token', {
      method: 'POST',
      headers: { accept: 'application/json', 'content-type': 'application/json' },
      body: JSON.stringify({
        client_id: env.GITHUB_CLIENT_ID,
        client_secret: env.GITHUB_CLIENT_SECRET,
        code,
        redirect_uri: callback.toString(),
        code_verifier: verifier,
      }),
      signal: AbortSignal.timeout(10_000),
      cache: 'no-store',
    });
    if (!tokenResponse.ok) throw new Error('GitHub token exchange failed.');
    const token = tokenSchema.parse(await tokenResponse.json());
    const userResponse = await fetch('https://api.github.com/user', {
      headers: {
        accept: 'application/vnd.github+json',
        authorization: `Bearer ${token.access_token}`,
        'x-github-api-version': '2026-03-10',
      },
      signal: AbortSignal.timeout(10_000),
      cache: 'no-store',
    });
    if (!userResponse.ok) throw new Error('GitHub identity lookup failed.');
    const user = userSchema.parse(await userResponse.json());
    const session = await createLoopSession(user);
    const response = NextResponse.redirect(
      new URL('/', env.APP_BASE_URL ?? request.nextUrl.origin),
    );
    response.cookies.set(SESSION_COOKIE, session, secureCookieOptions(SESSION_MAX_AGE_SECONDS));
    clearOauthCookies(response);
    return response;
  } catch {
    return failed(request, 'github_oauth_failed');
  }
}
