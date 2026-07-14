import { createHash, randomBytes } from 'node:crypto';
import { NextResponse, type NextRequest } from 'next/server';
import { env } from '@/lib/env';
import { OAUTH_STATE_COOKIE, OAUTH_VERIFIER_COOKIE, secureCookieOptions } from '@/lib/session';

export const runtime = 'nodejs';

function base64url(value: Buffer): string {
  return value.toString('base64url');
}

export async function GET(request: NextRequest) {
  if (!env.GITHUB_CLIENT_ID || !env.GITHUB_CLIENT_SECRET || !env.LOOP_SESSION_SECRET) {
    return NextResponse.json({ error: 'GitHub sign-in is not configured.' }, { status: 503 });
  }
  const state = base64url(randomBytes(32));
  const verifier = base64url(randomBytes(48));
  const challenge = base64url(createHash('sha256').update(verifier).digest());
  const callback = new URL('/api/auth/github/callback', env.APP_BASE_URL ?? request.nextUrl.origin);
  const authorization = new URL('https://github.com/login/oauth/authorize');
  authorization.searchParams.set('client_id', env.GITHUB_CLIENT_ID);
  authorization.searchParams.set('redirect_uri', callback.toString());
  authorization.searchParams.set('scope', 'read:user');
  authorization.searchParams.set('state', state);
  authorization.searchParams.set('code_challenge', challenge);
  authorization.searchParams.set('code_challenge_method', 'S256');

  const response = NextResponse.redirect(authorization);
  const options = { ...secureCookieOptions(600), path: '/api/auth/github' };
  response.cookies.set(OAUTH_STATE_COOKIE, state, options);
  response.cookies.set(OAUTH_VERIFIER_COOKIE, verifier, options);
  return response;
}
