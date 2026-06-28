'use client';

export default function Error({ reset }: { error: Error; reset: () => void }) {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4">
      <h1 className="text-2xl font-semibold">Something went wrong</h1>
      <button
        onClick={reset}
        className="rounded-md border border-current px-4 py-2 text-sm hover:opacity-80"
      >
        Try again
      </button>
    </main>
  );
}
