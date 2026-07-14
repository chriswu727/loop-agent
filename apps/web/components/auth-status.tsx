import { cookies } from 'next/headers';
import { env } from '@/lib/env';
import { SESSION_COOKIE, verifyLoopSession } from '@/lib/session';

export async function AuthStatus() {
  if (!env.WEB_AUTH_REQUIRED && !env.GITHUB_CLIENT_ID) return null;
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  const session = token ? await verifyLoopSession(token).catch(() => null) : null;

  return (
    <div className="mx-auto flex max-w-3xl justify-end px-6 pt-4 text-xs">
      {session ? (
        <form action="/api/auth/logout" method="post" className="flex items-center gap-3">
          <span className="opacity-60">Signed in as {session.login}</span>
          <button type="submit" className="underline opacity-70 hover:opacity-100">
            Sign out
          </button>
        </form>
      ) : (
        <a href="/api/auth/github/login" className="underline opacity-70 hover:opacity-100">
          Sign in with GitHub
        </a>
      )}
    </div>
  );
}
