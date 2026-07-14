import type { NextRequest } from 'next/server';
import { env } from '@/lib/env';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const base = env.API_INTERNAL_URL;
  const suffix = path[0] === 'api' && path[1] === 'v1' ? path.slice(2) : path;
  const target = new URL(`/api/v1/${suffix.join('/')}${request.nextUrl.search}`, base);
  const headers = new Headers(request.headers);
  headers.delete('host');
  headers.delete('content-length');
  headers.delete('cookie');
  const session = request.cookies.get('loop_session')?.value;
  const supplied = request.headers.get('authorization');
  const authorization = supplied?.toLowerCase().startsWith('bearer ')
    ? supplied
    : session
      ? `Bearer ${session}`
      : !env.WEB_AUTH_REQUIRED && env.LOOP_API_TOKEN
        ? `Bearer ${env.LOOP_API_TOKEN}`
        : null;
  if (!authorization) {
    return Response.json({ detail: 'Authentication required.' }, { status: 401 });
  }
  headers.set('authorization', authorization);

  const body =
    request.method === 'GET' || request.method === 'HEAD' ? undefined : await request.arrayBuffer();
  const upstream = await fetch(target, {
    method: request.method,
    headers,
    body,
    redirect: 'manual',
    cache: 'no-store',
  });
  const responseHeaders = new Headers(upstream.headers);
  responseHeaders.delete('content-encoding');
  responseHeaders.delete('content-length');
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
