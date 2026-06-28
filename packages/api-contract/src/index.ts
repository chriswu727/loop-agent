/**
 * Shared API contract types — the single source of truth between frontend and
 * backend. Hand-written to mirror the FastAPI Pydantic schemas; for the full
 * surface you can codegen from the live OpenAPI spec at `/openapi.json`.
 */

export type TaskStatus =
  | 'pending'
  | 'running'
  | 'awaiting_input'
  | 'completed'
  | 'cancelled'
  | 'failed';

export type StopReason =
  | 'goal_achieved'
  | 'max_steps'
  | 'budget_exhausted'
  | 'stuck'
  | 'cancelled'
  | 'error';

export type StepStatus = 'ok' | 'error' | 'blocked';

export interface Limits {
  max_steps: number;
  token_budget: number;
}

export interface Task {
  id: string;
  goal: string;
  status: TaskStatus;
  rubric: string[];
  pending_question: string | null;
  limits: Limits;
  summary: string | null;
  verification_score: number;
  verified_by: 'execution' | 'judgment' | null;
  receipt_hash: string | null;
  steps_used: number;
  tokens_used: number;
  workspace_path: string | null;
  stop_reason: StopReason | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface Step {
  id: string;
  task_id: string;
  number: number;
  thought: string;
  tool: string;
  tool_args: Record<string, unknown>;
  observation: string;
  status: StepStatus;
  tokens: number;
  created_at: string;
}

export interface LimitDefaults {
  max_steps_default: number;
  max_steps_cap: number;
  token_budget_default: number;
  token_budget_cap: number;
}

export interface FileEntry {
  path: string;
  size: number;
}

export interface FileContent {
  path: string;
  content: string;
  size: number;
  truncated: boolean;
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
