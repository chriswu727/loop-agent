'use client';

import type { Task } from '@repo/api-contract';
import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';
import { tasksApi } from '@/lib/api-client';

interface Msg {
  role: 'user' | 'agent';
  text: string;
  taskId?: string;
  status?: string;
}

const TERMINAL = new Set(['completed', 'stopped', 'cancelled', 'failed']);

function replyFor(t: Task): string {
  if (t.status === 'awaiting_input') return t.pending_question ?? 'I need a bit more to continue.';
  if (t.status === 'completed')
    return t.stop_reason === 'goal_achieved'
      ? t.summary || 'Done.'
      : `Stopped (${t.stop_reason}). ${t.summary ?? ''}`.trim();
  if (t.status === 'stopped') return `Stopped (${t.stop_reason}). ${t.summary ?? ''}`.trim();
  if (t.status === 'failed') return `Failed: ${t.error ?? 'unknown error'}`;
  return `…${t.status}`;
}

export default function ChatPage() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const sessionId = useRef<string>('');
  // State (not a ref): the placeholder reads it during render, and it drives whether
  // the next message answers an open question or starts a new task.
  const [pendingTask, setPendingTask] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let sid = localStorage.getItem('loop-chat-session');
    if (!sid) {
      sid = crypto.randomUUID();
      localStorage.setItem('loop-chat-session', sid);
    }
    sessionId.current = sid;
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  function update(taskId: string, patch: Partial<Msg>) {
    setMessages((m) => m.map((msg) => (msg.taskId === taskId ? { ...msg, ...patch } : msg)));
  }

  async function pollUntilDone(taskId: string) {
    for (let i = 0; i < 120; i++) {
      await new Promise((r) => setTimeout(r, 1500));
      let t: Task;
      try {
        t = await tasksApi.get(taskId);
      } catch {
        continue;
      }
      update(taskId, { text: replyFor(t), status: t.status });
      if (t.status === 'awaiting_input') {
        setPendingTask(taskId);
        return;
      }
      if (TERMINAL.has(t.status)) return;
    }
  }

  async function send() {
    const text = input.trim();
    if (text.length < 1 || busy) return;
    setInput('');
    setBusy(true);
    setMessages((m) => [...m, { role: 'user', text }]);
    try {
      let task: Task;
      if (pendingTask) {
        task = await tasksApi.respond(pendingTask, text); // answer the open question
        setPendingTask(null);
      } else {
        task = await tasksApi.publish({ goal: text, chat_id: sessionId.current });
      }
      setMessages((m) => [
        ...m,
        { role: 'agent', text: '…thinking', taskId: task.id, status: task.status },
      ]);
      await pollUntilDone(task.id);
    } catch {
      setMessages((m) => [...m, { role: 'agent', text: 'Could not reach the agent.' }]);
    } finally {
      setBusy(false);
    }
  }

  function newChat() {
    const sid = crypto.randomUUID();
    localStorage.setItem('loop-chat-session', sid);
    sessionId.current = sid;
    setPendingTask(null);
    setMessages([]);
  }

  return (
    <main className="mx-auto flex h-[100dvh] max-w-2xl flex-col px-4 py-6">
      <header className="mb-3 flex items-center justify-between">
        <Link href="/" className="text-sm opacity-60 transition hover:opacity-100">
          ← Home
        </Link>
        <h1 className="text-sm font-medium">Chat</h1>
        <button onClick={newChat} className="text-sm opacity-60 transition hover:opacity-100">
          New chat
        </button>
      </header>

      <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto pb-3">
        {messages.length === 0 && (
          <p className="mt-10 text-center text-sm opacity-40">
            Talk to Loop. Each message runs a verified task; follow-ups keep the context.
          </p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={m.role === 'user' ? 'flex justify-end' : 'flex justify-start'}>
            <div
              className={
                m.role === 'user'
                  ? 'max-w-[80%] rounded-2xl rounded-br-sm bg-blue-600 px-3.5 py-2 text-sm text-white'
                  : 'max-w-[80%] rounded-2xl rounded-bl-sm border border-black/10 bg-white/60 px-3.5 py-2 text-sm dark:border-white/10 dark:bg-white/[0.04]'
              }
            >
              <p className="whitespace-pre-wrap">{m.text}</p>
              {m.taskId && (
                <Link
                  href={`/tasks/${m.taskId}`}
                  className="mt-1 block text-[11px] opacity-40 hover:opacity-80"
                >
                  view run
                </Link>
              )}
            </div>
          </div>
        ))}
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
        className="flex items-end gap-2 border-t border-black/10 pt-3 dark:border-white/10"
      >
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          rows={1}
          placeholder={pendingTask ? 'Answer the agent…' : 'Message Loop…'}
          className="max-h-32 flex-1 resize-none rounded-xl border border-black/10 bg-transparent px-3 py-2 text-sm outline-none focus:border-blue-500/60 dark:border-white/15"
        />
        <button
          type="submit"
          disabled={busy || input.trim().length < 1}
          className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-blue-500 disabled:opacity-40"
        >
          {busy ? '…' : 'Send'}
        </button>
      </form>
    </main>
  );
}
