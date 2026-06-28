import { NextResponse } from 'next/server';

// The frontend's own liveness endpoint (used by the Kubernetes probe).
export const dynamic = 'force-dynamic';

export function GET() {
  return NextResponse.json({ status: 'ok', service: 'web' });
}
