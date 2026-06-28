import Link from 'next/link';

export default function NotFound() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4">
      <h1 className="text-3xl font-bold">404</h1>
      <p className="opacity-70">This page could not be found.</p>
      <Link href="/" className="underline">
        Go home
      </Link>
    </main>
  );
}
