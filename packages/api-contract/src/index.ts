/**
 * Shared API contract types — the single source of truth between frontend and
 * backend. Hand-written to mirror the FastAPI Pydantic schemas; for the full
 * surface you can codegen from the live OpenAPI spec at `/openapi.json`.
 */

export type TaskStatus =
  'pending' | 'running' | 'awaiting_input' | 'completed' | 'cancelled' | 'failed';

export type StopReason =
  'goal_achieved' | 'max_steps' | 'budget_exhausted' | 'stuck' | 'cancelled' | 'error';

export type StepStatus = 'ok' | 'error' | 'blocked';

export type Capability =
  | 'fs.read'
  | 'fs.write'
  | 'exec'
  | 'net.shell'
  | 'net.browser'
  | 'email.read'
  | 'email.send'
  | 'calendar.read'
  | 'calendar.write'
  | 'vision'
  | 'memory.read'
  | 'memory.write'
  | 'task.spawn';

export interface Authority {
  schema: string;
  requested: Capability[] | null;
  resolved: Capability[];
  egress_hosts: string[];
  sandbox: string | null;
  enforcement: {
    provider_gateway: boolean;
    browser_gateway: boolean;
    email_gateway: boolean;
    calendar_gateway: boolean;
    vision_gateway: boolean;
    egress_proxy: boolean;
  };
  audit: Array<{
    id?: string;
    at?: string;
    kind: 'provider' | 'egress' | 'audit';
    decision: 'allowed' | 'blocked' | 'unavailable';
    tool?: string | null;
    target?: string | null;
    host?: string | null;
    port?: number | null;
    reason?: string | null;
  }>;
}

export interface Limits {
  max_steps: number;
  token_budget: number;
}

export interface Task {
  id: string;
  goal: string;
  owner_id: string;
  project_id: string;
  status: TaskStatus;
  rubric: string[];
  pending_question: string | null;
  allowed_tools: string[] | null;
  authority: Authority;
  allow_egress: boolean;
  egress_hosts: string[] | null;
  require_approval: boolean;
  use_browser: boolean;
  use_email: boolean;
  use_calendar: boolean;
  use_vision: boolean;
  skill: string | null;
  parent_id: string | null;
  depth: number;
  idempotency_key: string | null;
  attempt: number;
  limits: Limits;
  summary: string | null;
  verification_score: number;
  verified_by: 'execution' | 'judgment' | null;
  receipt_hash: string | null;
  sandbox: string | null;
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
  prev_hash: string | null;
  hash: string;
  created_at: string;
}

export interface LedgerStatus {
  verified: boolean;
  head: string;
  length: number;
  broken_at: number | null;
}

export interface SkillInfo {
  name: string;
  description: string;
  verified: boolean;
  reason: string;
  allowed_tools: string[] | null;
  capabilities: Capability[] | null;
  allow_egress: boolean;
  egress_hosts: string[] | null;
}

export interface Trigger {
  id: string;
  name: string;
  goal: string;
  owner_id: string;
  project_id: string;
  enabled: boolean;
  fire_count: number;
  secret: string;
  max_steps: number;
  token_budget: number;
  allowed_tools: string[] | null;
  capabilities: Capability[] | null;
  allow_egress: boolean;
  egress_hosts: string[] | null;
  require_approval: boolean;
  skill: string | null;
  interval_minutes: number | null;
  last_fired_at: string | null;
  created_at: string;
  updated_at: string;
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
