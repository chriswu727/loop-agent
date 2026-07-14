import type { Metadata } from 'next';
import { AuthStatus } from '@/components/auth-status';
import { env } from '@/lib/env';
import './globals.css';

export const metadata: Metadata = {
  title: env.NEXT_PUBLIC_APP_NAME,
  description:
    'Publish a task; an agent drafts it, critiques itself, and improves it pass by pass — within the limits you set.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">
        <AuthStatus />
        {children}
      </body>
    </html>
  );
}
