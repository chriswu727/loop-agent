/**
 * Typed, validated environment access.
 *
 * Validates at module load so a missing/malformed variable is a build-time
 * failure, not a blank screen in production. Server-only values must never be
 * prefixed with `NEXT_PUBLIC_`.
 */
import { z } from 'zod';

const schema = z
  .object({
    NEXT_PUBLIC_APP_NAME: z.string().min(1).default('Loop'),
    API_INTERNAL_URL: z.string().url().default('http://localhost:8000'),
    APP_BASE_URL: z.string().url().optional(),
    LOOP_API_TOKEN: z.string().min(16).optional(),
    LOOP_SESSION_SECRET: z.string().min(32).optional(),
    WEB_AUTH_REQUIRED: z
      .enum(['true', 'false'])
      .default('false')
      .transform((value) => value === 'true'),
    GITHUB_CLIENT_ID: z.string().min(1).optional(),
    GITHUB_CLIENT_SECRET: z.string().min(1).optional(),
  })
  .superRefine((value, context) => {
    if (!value.WEB_AUTH_REQUIRED) return;
    for (const key of [
      'APP_BASE_URL',
      'LOOP_SESSION_SECRET',
      'GITHUB_CLIENT_ID',
      'GITHUB_CLIENT_SECRET',
    ] as const) {
      if (!value[key]) {
        context.addIssue({
          code: 'custom',
          path: [key],
          message: `${key} is required when WEB_AUTH_REQUIRED=true`,
        });
      }
    }
  });

const parsed = schema.safeParse({
  NEXT_PUBLIC_APP_NAME: process.env.NEXT_PUBLIC_APP_NAME,
  API_INTERNAL_URL: process.env.API_INTERNAL_URL,
  APP_BASE_URL: process.env.APP_BASE_URL,
  LOOP_API_TOKEN: process.env.LOOP_API_TOKEN,
  LOOP_SESSION_SECRET: process.env.LOOP_SESSION_SECRET,
  WEB_AUTH_REQUIRED: process.env.WEB_AUTH_REQUIRED,
  GITHUB_CLIENT_ID: process.env.GITHUB_CLIENT_ID,
  GITHUB_CLIENT_SECRET: process.env.GITHUB_CLIENT_SECRET,
});

if (!parsed.success) {
  console.error('Invalid environment variables:', parsed.error.flatten().fieldErrors);
  throw new Error('Invalid environment variables — see logs above.');
}

export const env = parsed.data;

/**
 * Server-side code reaches the API over the cluster network. Browser calls stay
 * on the web origin and pass through the server-side proxy.
 */
export function apiBaseUrl(): string {
  if (typeof window === 'undefined') {
    return env.API_INTERNAL_URL;
  }
  return '/api/loop';
}

export function serverApiToken(): string | undefined {
  return typeof window === 'undefined' ? env.LOOP_API_TOKEN : undefined;
}
