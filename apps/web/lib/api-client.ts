/**
 * Single typed API client. Wraps `fetch` with base URL resolution, timeouts,
 * request-id propagation, and error normalization so callers never repeat this.
 */
import type { LimitDefaults, Page, Step, Task } from '@repo/api-contract';
import { apiBaseUrl } from './env';

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
    const res = await fetch(`${apiBaseUrl()}${path}`, {
      ...rest,
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json', ...headers },
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
export type { LimitDefaults, Page, Step, Task };

export interface PublishBody {
  goal: string;
  limits?: {
    max_steps?: number;
    token_budget?: number;
  };
}

export const tasksApi = {
  limits: () => apiFetch<LimitDefaults>('/api/v1/tasks/limits'),
  list: (params?: { limit?: number; offset?: number }) =>
    apiFetch<Page<Task>>(
      `/api/v1/tasks?limit=${params?.limit ?? 50}&offset=${params?.offset ?? 0}`,
    ),
  get: (id: string) => apiFetch<Task>(`/api/v1/tasks/${id}`),
  steps: (id: string) => apiFetch<Step[]>(`/api/v1/tasks/${id}/steps`),
  publish: (body: PublishBody) =>
    apiFetch<Task>('/api/v1/tasks', { method: 'POST', body: JSON.stringify(body) }),
  cancel: (id: string) =>
    apiFetch<Task>(`/api/v1/tasks/${id}/cancel`, { method: 'POST' }),
};
