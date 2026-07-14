/**
 * Single typed API client. Wraps `fetch` with base URL resolution, timeouts,
 * request-id propagation, and error normalization so callers never repeat this.
 */
import type {
  FileContent,
  FileEntry,
  LedgerStatus,
  LimitDefaults,
  Page,
  Step,
  Task,
  Trigger,
} from '@repo/api-contract';
import { apiBaseUrl, serverApiToken } from './env';

/** Normalized error mirroring the backend's RFC 9457 problem+json body. */
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly requestId?: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

interface RequestOptions extends RequestInit {
  timeoutMs?: number;
}

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { timeoutMs = 10_000, headers, ...rest } = options;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const token = serverApiToken();
    const res = await fetch(`${apiBaseUrl()}${path}`, {
      ...rest,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...headers,
      },
    });

    const requestId = res.headers.get('x-request-id') ?? undefined;

    if (!res.ok) {
      const problem = (await res.json().catch(() => ({}))) as {
        code?: string;
        detail?: string;
      };
      throw new ApiError(
        res.status,
        problem.code ?? 'error',
        problem.detail ?? res.statusText,
        requestId,
      );
    }

    if (res.status === 204) {
      return undefined as T;
    }
    return (await res.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

// ---------------------------------------------------------------------------
// Typed client for the agent-loop API. Types come from @repo/api-contract.
// ---------------------------------------------------------------------------
export type { FileContent, FileEntry, LedgerStatus, LimitDefaults, Page, Step, Task };

export interface PublishBody {
  goal: string;
  project_id?: string;
  autostart?: boolean;
  allowed_tools?: string[] | null;
  capabilities?: import('@repo/api-contract').Capability[] | null;
  allow_egress?: boolean;
  egress_hosts?: string[] | null;
  require_approval?: boolean;
  use_browser?: boolean;
  use_email?: boolean;
  use_calendar?: boolean;
  use_vision?: boolean;
  chat_id?: string | null;
  skill?: string | null;
  idempotency_key?: string;
  limits?: {
    max_steps?: number;
    token_budget?: number;
  };
}

/** Multipart upload — bypasses apiFetch's JSON content-type. */
export async function uploadFile(taskId: string, file: File): Promise<FileEntry[]> {
  const body = new FormData();
  body.append('file', file);
  const res = await fetch(`${apiBaseUrl()}/api/v1/tasks/${taskId}/files`, {
    method: 'POST',
    body,
  });
  if (!res.ok) {
    const problem = (await res.json().catch(() => ({}))) as { code?: string; detail?: string };
    throw new ApiError(res.status, problem.code ?? 'error', problem.detail ?? res.statusText);
  }
  return (await res.json()) as FileEntry[];
}

export const tasksApi = {
  limits: () => apiFetch<LimitDefaults>('/api/v1/tasks/limits'),
  list: (params?: { limit?: number; offset?: number }) =>
    apiFetch<Page<Task>>(
      `/api/v1/tasks?limit=${params?.limit ?? 50}&offset=${params?.offset ?? 0}`,
    ),
  get: (id: string) => apiFetch<Task>(`/api/v1/tasks/${id}`),
  children: (id: string) => apiFetch<Task[]>(`/api/v1/tasks/${id}/children`),
  steps: (id: string) => apiFetch<Step[]>(`/api/v1/tasks/${id}/steps`),
  ledger: (id: string) => apiFetch<LedgerStatus>(`/api/v1/tasks/${id}/ledger`),
  receipt: (id: string) =>
    apiFetch<{ receipt: Record<string, unknown>; valid: boolean; recomputed_hash: string }>(
      `/api/v1/tasks/${id}/receipt`,
    ),
  replayReceipt: (id: string) =>
    apiFetch<{ passed: boolean; checks: Array<Record<string, unknown>> }>(
      `/api/v1/tasks/${id}/receipt/replay`,
      { method: 'POST', timeoutMs: 180_000 },
    ),
  files: (id: string) => apiFetch<FileEntry[]>(`/api/v1/tasks/${id}/files`),
  fileContent: (id: string, path: string) =>
    apiFetch<FileContent>(`/api/v1/tasks/${id}/files/${path}`),
  downloadUrl: (id: string, path: string) =>
    `${apiBaseUrl()}/api/v1/tasks/${id}/download/${path}`,
  publish: (body: PublishBody) =>
    apiFetch<Task>('/api/v1/tasks', { method: 'POST', body: JSON.stringify(body) }),
  cancel: (id: string) =>
    apiFetch<Task>(`/api/v1/tasks/${id}/cancel`, { method: 'POST' }),
  retry: (id: string) =>
    apiFetch<Task>(`/api/v1/tasks/${id}/retry`, { method: 'POST' }),
  upload: uploadFile,
  start: (id: string) => apiFetch<Task>(`/api/v1/tasks/${id}/start`, { method: 'POST' }),
  respond: (id: string, answer: string) =>
    apiFetch<Task>(`/api/v1/tasks/${id}/respond`, {
      method: 'POST',
      body: JSON.stringify({ answer }),
    }),
};

export const memoryApi = {
  get: () => apiFetch<{ content: string }>('/api/v1/memory'),
};

export interface TriggerCreateBody {
  name: string;
  goal: string;
  limits?: { max_steps?: number; token_budget?: number };
  allowed_tools?: string[] | null;
  capabilities?: import('@repo/api-contract').Capability[] | null;
  allow_egress?: boolean;
  require_approval?: boolean;
  skill?: string | null;
  interval_minutes?: number | null;
}

export type { Trigger };

export const triggersApi = {
  list: () => apiFetch<Trigger[]>('/api/v1/triggers'),
  create: (body: TriggerCreateBody) =>
    apiFetch<Trigger>('/api/v1/triggers', { method: 'POST', body: JSON.stringify(body) }),
  fire: (id: string, secret: string) =>
    apiFetch<Task>(`/api/v1/triggers/${id}/fire`, {
      method: 'POST',
      headers: { 'X-Trigger-Secret': secret },
    }),
  remove: (id: string) =>
    apiFetch<void>(`/api/v1/triggers/${id}`, { method: 'DELETE' }),
};
