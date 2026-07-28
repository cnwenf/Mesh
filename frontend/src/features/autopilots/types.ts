/**
 * Autopilot 实体类型(autopilot.md §2.2–§2.5 / §3.1)。
 * 时间一律 UTC RFC3339 字符串;id 一律 UUID 字符串。
 *
 * 规则 = 触发器 + 过滤 + 顺序动作 + 护栏(默认开启);运行记录(run)承载
 * 触发快照 / 级联谱系 / token 统计;webhook 凭据明文仅在创建/轮换响应出现一次。
 */

export type AutopilotTriggerType =
  | 'schedule'
  | 'issue_status_changed'
  | 'issue_created'
  | 'issue_field_changed'
  | 'comment_created'
  | 'agent_mentioned'
  | 'webhook_received';

export const TRIGGER_TYPES: ReadonlyArray<AutopilotTriggerType> = [
  'schedule',
  'issue_status_changed',
  'issue_created',
  'issue_field_changed',
  'comment_created',
  'agent_mentioned',
  'webhook_received',
];

/** 规则状态机(§4.4)。 */
export type AutopilotStatus = 'active' | 'paused' | 'archived';

/** 运行状态机(§4.4)。 */
export type AutopilotRunStatus =
  | 'pending'
  | 'running'
  | 'waiting_approval'
  | 'retrying'
  | 'succeeded'
  | 'failed'
  | 'cancelled';

export type RetryBackoff = 'fixed' | 'linear' | 'exponential';

export type ActionKind =
  | 'run_agent_prompt'
  | 'add_comment'
  | 'send_notification'
  | 'create_issue'
  | 'http_request';

export const ACTION_KINDS: ReadonlyArray<ActionKind> = [
  'run_agent_prompt',
  'add_comment',
  'send_notification',
  'create_issue',
  'http_request',
];

/** §2.6 护栏配置(创建时即以默认值生效)。 */
export interface Guardrails {
  readonly rate_limit_overflow: 'drop' | 'queue' | 'alert_only';
  readonly dedup_window_seconds: number;
  readonly dedup_key_template: string;
  readonly daily_run_budget: number;
  readonly daily_token_budget: number;
  readonly approval_required_actions: ReadonlyArray<string>;
  readonly kill_switch_paused: boolean;
  readonly agent_loop_detection: boolean;
  readonly cascade_max_depth: number;
  readonly agent_loop_window_seconds: number;
}

/** 动作项(§2.6 数组,顺序执行)。字段按动作类型取用。 */
export interface ActionConfigItem {
  readonly type: ActionKind;
  readonly executor_agent_id?: string;
  readonly prompt?: string;
  readonly content?: string;
  readonly message?: string;
  readonly title?: string;
  readonly description?: string;
  readonly project_id?: string;
  readonly priority?: string;
  readonly url?: string;
  readonly method?: string;
  readonly to?: ReadonlyArray<string>;
}

export interface AutopilotRule {
  readonly id: string;
  readonly workspace_id: string;
  readonly name: string;
  readonly description: string | null;
  readonly trigger_type: AutopilotTriggerType;
  readonly trigger_config: Readonly<Record<string, unknown>>;
  readonly filter_config: Readonly<Record<string, unknown>>;
  readonly action_config: ReadonlyArray<ActionConfigItem>;
  readonly executor_agent_id: string | null;
  readonly status: AutopilotStatus;
  readonly guardrails: Guardrails;
  readonly max_retries: number;
  readonly retry_backoff: RetryBackoff;
  readonly retry_base_seconds: number;
  readonly retry_max_seconds: number;
  readonly rate_limit_max: number;
  readonly rate_limit_window_seconds: number;
  readonly concurrency_limit: number;
  readonly require_approval: boolean;
  readonly next_run_at: string | null;
  readonly last_run_at: string | null;
  readonly last_run_status: AutopilotRunStatus | null;
  readonly created_by: string;
  readonly created_at: string;
  readonly updated_at: string;
  readonly stats: { readonly runs_30d: number; readonly success_rate: number | null } | null;
}

export interface RunAttempt {
  readonly attempt_number: number;
  readonly status: string;
  readonly execution_id: string | null;
  readonly started_at: string | null;
  readonly finished_at: string | null;
  readonly error: Readonly<Record<string, unknown>> | null;
  readonly prompt_tokens: number | null;
  readonly completion_tokens: number | null;
}

export interface RunArtifact {
  readonly id: string;
  readonly artifact_type: 'comment' | 'issue' | 'notification' | 'agent_output' | 'http_response';
  readonly ref_table: string;
  readonly ref_id: string;
  readonly summary: string | null;
  readonly created_at: string;
}

export interface AutopilotRun {
  readonly id: string;
  readonly autopilot_id: string;
  readonly workspace_id: string;
  readonly trigger_type: AutopilotTriggerType;
  readonly trigger_snapshot: Readonly<Record<string, unknown>>;
  readonly webhook_event_id: string | null;
  readonly execution_id: string | null;
  readonly parent_run_id: string | null;
  readonly cascade_depth: number;
  readonly status: AutopilotRunStatus;
  readonly started_at: string | null;
  readonly finished_at: string | null;
  readonly duration_ms: number | null;
  readonly retry_count: number;
  readonly error: Readonly<Record<string, unknown>> | null;
  readonly prompt_tokens: number | null;
  readonly completion_tokens: number | null;
  readonly total_tokens: number;
  readonly triggered_by: string | null;
  readonly is_test: boolean;
  readonly created_at: string;
  readonly updated_at: string;
  readonly attempts?: ReadonlyArray<RunAttempt>;
  readonly artifacts?: ReadonlyArray<RunArtifact>;
}

/** §3.2:webhook 凭据创建/轮换响应——token + secret 仅此一次明文。 */
export interface WebhookSecretCreated {
  readonly id: string;
  readonly label: string;
  readonly status: string;
  readonly token: string;
  readonly secret: string;
  readonly created_at: string;
}

/** 列表渲染面——绝不含 token / secret / 密文(§5.3 红线)。 */
export interface WebhookSecretPublic {
  readonly id: string;
  readonly label: string;
  readonly status: string;
  readonly created_at: string;
  readonly revoked_at: string | null;
}

export interface KillSwitchResult {
  readonly kill_switch: boolean;
  readonly paused_autopilots: number;
  readonly reason?: string | null;
  readonly updated_at: string;
}

export interface SchedulePreview {
  readonly cron: string | null;
  readonly timezone: string | null;
  readonly next_runs: ReadonlyArray<string>;
}

export interface TestRunResult {
  readonly run_id?: string;
  readonly status?: string;
  readonly autopilot_id?: string;
  readonly is_test?: boolean;
  readonly would_run?: boolean;
  readonly matched_filters?: Readonly<Record<string, unknown>>;
}

/** 入站 webhook 裸 JSON 契约(§3.2,非 §6.14 包络)。 */
export interface InboundWebhookResponse {
  readonly received?: boolean;
  readonly event_id?: string;
  readonly process_status?: string;
  readonly run_id?: string | null;
  readonly error?: { readonly code: string; readonly message: string };
}

/** Inbound webhook event audit row (autopilot.md §2.5 / §4.1 最近事件). */
export interface WebhookEventItem {
  readonly id: string;
  readonly autopilot_id: string | null;
  readonly idempotency_key: string;
  readonly event_type: string;
  readonly headers: Readonly<Record<string, string>> | null;
  readonly payload: Readonly<Record<string, unknown>>;
  readonly signature_status: 'valid' | 'invalid' | 'missing' | 'skipped';
  readonly process_status:
    | 'received'
    | 'matched'
    | 'dispatched'
    | 'deduped'
    | 'rejected'
    | 'processed'
    | 'failed';
  readonly received_at: string;
}
