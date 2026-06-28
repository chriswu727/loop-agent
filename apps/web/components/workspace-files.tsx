'use client';

import type { FileEntry } from '@repo/api-contract';
import { useState } from 'react';
import { tasksApi } from '@/lib/api-client';

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} KB`;
}

/** Browse and download the files the agent produced in its workspace. */
export function WorkspaceFiles({ taskId, files }: { taskId: string; files: FileEntry[] }) {
  const [openPath, setOpenPath] = useState<string | null>(null);
  const [content, setContent] = useState<string>('');
  const [loading, setLoading] = useState(false);

  async function open(path: string) {
    if (openPath === path) {
      setOpenPath(null);
      return;
    }
    setOpenPath(path);
    setLoading(true);
    try {
      const file = await tasksApi.fileContent(taskId, path);
      setContent(file.truncated ? `${file.content}\n... [truncated]` : file.content);
    } catch {
      setContent('(could not read file)');
    } finally {
      setLoading(false);
    }
  }

  if (files.length === 0) return null;

  return (
    <section className="mt-6">
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide opacity-50">
        Output files ({files.length})
      </h2>
      <div className="overflow-hidden rounded-xl border border-black/10 dark:border-white/10">
        {files.map((f) => (
          <div key={f.path} className="border-b border-black/5 last:border-0 dark:border-white/5">
            <div className="flex items-center justify-between px-3 py-2">
              <button
                onClick={() => open(f.path)}
                className="truncate font-mono text-xs hover:text-blue-600 dark:hover:text-blue-400"
              >
                {f.path}
              </button>
              <div className="flex shrink-0 items-center gap-3 text-[11px]">
                <span className="tabular-nums opacity-40">{humanSize(f.size)}</span>
                <a
                  href={tasksApi.downloadUrl(taskId, f.path)}
                  className="opacity-60 hover:opacity-100"
                  download
                >
                  download
                </a>
              </div>
            </div>
            {openPath === f.path && (
              <pre className="max-h-80 overflow-auto whitespace-pre-wrap border-t border-black/5 bg-black/[0.03] px-3 py-2 font-mono text-[11px] leading-relaxed dark:border-white/5 dark:bg-black/20">
                {loading ? 'Loading…' : content}
              </pre>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
