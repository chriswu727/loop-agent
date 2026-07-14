import { NextResponse, type NextRequest } from 'next/server';
import { env } from '@/lib/env';
import { SESSION_COOKIE } from '@/lib/session';

export async function POST(request: NextRequest) {
  const response = NextResponse.redirect(
    new URL('/', env.APP_BASE_URL ?? request.nextUrl.origin),
    303,
  );
  response.cookies.delete(SESSION_COOKIE);
  return response;
}
