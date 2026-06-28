/**
 * Shared API contract types — the single source of truth between frontend and
 * backend. Hand-written to mirror the FastAPI Pydantic schemas; for the full
 * surface you can codegen from the live OpenAPI spec at `/openapi.json`.
 */

export type TaskStatus = 'pending' | 'running' | 'completed' | 'cancelled' | 'failed';

export type StopReason =
  | 'target_reached'
  | 'max_iterations'
  | 'budget_exhausted'
  | 'plateau'
  | 'cancelled'
  | 'error';

export interface Limits {
  max_iterations: number;
  token_budget: number;
  target_score: number;
}

export interface Task {
  id: string;
  goal: string;
  status: TaskStatus;
  rubric: string[];
  limits: Limits;
  best_score: number;
  best_artifact: string | null;
  iterations_used: number;
  tokens_used: number;
  stop_reason: StopReason | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface Iteration {
  id: string;
  task_id: string;
  number: number;
  artifact: string;
  score: number;
  critique: string;
  tokens: number;
  created_at: string;
}

export interface LimitDefaults {
  max_iterations_default: number;
  max_iterations_cap: number;
  token_budget_default: number;
  token_budget_cap: number;
  target_score_default: number;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

/** RFC 9457 problem+json — the shape of every API error. */
export interface Problem {
  type: string;
  title: string;
  status: number;
  detail: string | null;
  code: string;
  request_id: string | null;
}
