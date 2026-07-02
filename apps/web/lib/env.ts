/**
 * Typed, validated environment access.
 *
 * Validates at module load so a missing/malformed variable is a build-time
 * failure, not a blank screen in production. Only `NEXT_PUBLIC_*` values are
 * exposed to the browser; server-only values must never be prefixed with it.
 */
import { z } from 'zod';

const schema = z.object({
  // Public (inlined into the client bundle by Next.js)
  NEXT_PUBLIC_API_URL: z.string().url(),
  NEXT_PUBLIC_APP_NAME: z.string().min(1).default('Loop'),
  // Server-only (used by server components / route handlers)
  API_INTERNAL_URL: z.string().url().optional(),
});

const parsed = schema.safeParse({
  NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL,
  NEXT_PUBLIC_APP_NAME: process.env.NEXT_PUBLIC_APP_NAME,
  API_INTERNAL_URL: process.env.API_INTERNAL_URL,
});

if (!parsed.success) {
  console.error('Invalid environment variables:', parsed.error.flatten().fieldErrors);
  throw new Error('Invalid environment variables — see logs above.');
}

export const env = parsed.data;

/**
 * The base URL for API calls. Server-side code reaches the API over the internal
 * cluster URL (fast, never leaves the cluster); the browser uses the public URL.
 */
export function apiBaseUrl(): string {
  if (typeof window === 'undefined') {
    return env.API_INTERNAL_URL ?? env.NEXT_PUBLIC_API_URL;
  }
  return env.NEXT_PUBLIC_API_URL;
}
