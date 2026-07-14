import { jwtVerify, SignJWT, type JWTPayload } from 'jose';
import { env } from './env';

export const SESSION_COOKIE = 'loop_session';
export const OAUTH_STATE_COOKIE = 'loop_oauth_state';
export const OAUTH_VERIFIER_COOKIE = 'loop_oauth_verifier';
export const SESSION_MAX_AGE_SECONDS = 8 * 60 * 60;

export interface LoopSession extends JWTPayload {
  sub: string;
  login: string;
  name?: string;
  avatar_url?: string;
}

function key(): Uint8Array {
  if (!env.LOOP_SESSION_SECRET) throw new Error('Loop session signing is not configured.');
  return new TextEncoder().encode(env.LOOP_SESSION_SECRET);
}

export async function createLoopSession(user: {
  id: number;
  login: string;
  name?: string | null;
  avatar_url?: string | null;
}): Promise<string> {
  return new SignJWT({
    login: user.login,
    name: user.name ?? undefined,
    avatar_url: user.avatar_url ?? undefined,
  })
    .setProtectedHeader({ alg: 'HS256', typ: 'JWT' })
    .setSubject(`github:${user.id}`)
    .setIssuer('loop-web')
    .setAudience('loop-api')
    .setIssuedAt()
    .setExpirationTime(`${SESSION_MAX_AGE_SECONDS}s`)
    .sign(key());
}

export async function verifyLoopSession(token: string): Promise<LoopSession> {
  const { payload } = await jwtVerify(token, key(), {
    algorithms: ['HS256'],
    issuer: 'loop-web',
    audience: 'loop-api',
  });
  if (!payload.sub || typeof payload.login !== 'string') throw new Error('Invalid session claims.');
  return payload as LoopSession;
}

export function secureCookieOptions(maxAge: number) {
  return {
    httpOnly: true,
    sameSite: 'lax' as const,
    secure: process.env.NODE_ENV === 'production',
    path: '/',
    maxAge,
  };
}
