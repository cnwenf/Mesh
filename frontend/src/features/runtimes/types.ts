/**
 * Runtime / Execution 实体类型(runtime.md §2.2 / §3.1 / §3.3)。
 * 时间一律 UTC RFC3339 字符串;id 一律 UUID 字符串。
 *
 * 逻辑 execution 与物理 attempt 分层(§2.1,README §6.4):一次触发只有一行
 * `task_executions`(承载幂等键 / 入队快照 / 最终结果),每次领取新建一行
 * `execution_attempts`(runtime / 分支 / 日志 / 单次结果都挂在 attempt 上)。
 */

export type RuntimeKind = 'platform_managed' | 'self_hosted';

/** runtime 生命周期状态(runtime.md §2.2 runtimes.status)。 */
export type RuntimeStatus =
  'pending' | 'online' | 'unavailable' | 'paused' | 'draining' | 'decommissioned';

/** 逻辑执行状态(runtime.md §4.7,含审批挂起 awaiting_approval)。 */
export type ExecutionStatus =
  | 'queued'
  | 'claimed'
  | 'running'
  | 'cancelling'
  | 'awaiting_approval'
  | 'completed'
  | 'failed'
  | 'timeout'
  | 'cancelled';

/** 物理尝试状态(runtime.md §2.2 execution_attempts.status)。 */
export type AttemptStatus =
  | 'claimed'
  | 'running'
  | 'cancelling'
  | 'completed'
  | 'failed'
  | 'timeout'
  | 'cancelled'
  | 'reclaimed';

export type ExecutionTrigger =
  'assign' | 'mention' | 'autopilot' | 'manual' | 'chat' | 'integration';

/** runtime 列表 / 详情共享的元数据面(runtime.md §2.2 runtimes 表)。 */
export interface RuntimeSummary {
  readonly id: string;
  readonly name: string;
  readonly kind: RuntimeKind;
  readonly status: RuntimeStatus;
  /** 自定义标签,如 {"gpu":"true","region":"intranet"} */
  readonly labels: Readonly<Record<string, string>>;
  /** 已安装工具 / 能力列表,如 ["version_control","python"] */
  readonly capabilities: readonly string[];
  readonly hostname: string | null;
  readonly os: string | null;
  readonly cpu_cores: number | null;
  readonly memory_mb: number | null;
  readonly max_concurrent: number;
  readonly current_load: number;
  readonly last_heartbeat_at: string | null;
  readonly heartbeat_interval_seconds: number;
  readonly version: string | null;
  readonly created_at: string;
}

/** runtime 详情(创建响应 / GET 详情共用;created_at 之后含 updated_at)。 */
export interface RuntimeDetail extends RuntimeSummary {
  readonly updated_at?: string;
}

/** 签名发布包分发信息(runtime.md §3.1 安装安全:下载→校验→解包→受限激活)。 */
export interface RuntimeRelease {
  readonly artifact_url: string;
  readonly sha256: string;
  readonly signature_url: string;
  readonly signing_key_url: string;
}

/** 一次性激活码 + 安装信息(创建 runtime 响应,§3.1;激活码只显示一次)。 */
export interface RuntimeActivation {
  readonly code: string;
  readonly expires_at: string;
  readonly release: RuntimeRelease;
  readonly activate_hint: string;
}

/** POST /runtimes 响应:影子记录 + 一次性激活(§4.3 注册引导)。 */
export interface RuntimeWithActivation extends RuntimeDetail {
  readonly activation: RuntimeActivation;
}

/** 轮换 runtime API token 响应(§3.1 :rotate;明文仅此一次返回)。 */
export interface RotateTokenResult {
  readonly runtime_token: string;
  readonly token_id?: string;
}

/** 物理尝试摘要(runtime.md §2.2 execution_attempts;requeue 新建行不覆盖旧行)。 */
export interface AttemptSummary {
  readonly id: string;
  readonly attempt_number: number;
  readonly runtime_name?: string | null;
  readonly runtime_id?: string | null;
  readonly status: AttemptStatus;
  readonly claimed_at: string | null;
  readonly started_at: string | null;
  readonly finished_at: string | null;
  readonly working_branch: string | null;
  /** 本次尝试结果(exit code / diff 摘要 / 产物引用,§4.4 产物 Tab)。 */
  readonly result?: Readonly<Record<string, unknown>> | null;
  readonly failure_reason: string | null;
}

/**
 * 逻辑执行摘要(runtime.md §2.2 task_executions)。
 *
 * 字段集与后端 `_render_execution` 逐一对齐(契约保真):后端不返回
 * agent_name / issue_identifier 等联表展示名,展示层标签统一由
 * trigger + 短 ID + 相对时间组成(executionLabel.ts),不依赖幽灵字段。
 */
export interface ExecutionSummary {
  readonly id: string;
  readonly agent_id: string | null;
  readonly issue_id: string | null;
  readonly trigger: ExecutionTrigger;
  readonly status: ExecutionStatus;
  readonly priority: number;
  readonly required_capabilities: readonly string[];
  readonly label_requirements: Readonly<Record<string, string>>;
  readonly timeout_seconds: number;
  readonly queued_at: string;
  readonly finished_at: string | null;
  readonly failure_reason: string | null;
  readonly result: Readonly<Record<string, unknown>> | null;
}

/** 执行详情:摘要 + 尝试链(§4.4 详情页;retry_count = attempts 数 − 1)。 */
export interface ExecutionDetail extends ExecutionSummary {
  readonly max_attempts: number;
  readonly attempts: readonly AttemptSummary[];
  readonly cancel_requested_at?: string | null;
  /** 凭证元信息(§4.10:仅名称 / 种类,值恒为 ***;服务端永不回显明文)。 */
  readonly credentials?: readonly CredentialMeta[];
}

/** 日志单行(REST 补历史;offset 为该执行累计字节偏移,单调递增,§2.3)。 */
export interface LogLine {
  readonly stream: string;
  readonly offset: number;
  readonly line: string;
}

/** REST 日志页(GET /executions/{id}/logs?offset=N,§3.1 续传)。 */
export interface ExecutionLogPage {
  readonly lines: readonly LogLine[];
  readonly next_offset: number;
}

/**
 * 日志流帧(§3.3:WS 主通道 execution:{id}:logs 与 SSE 降级共用同一帧型)。
 * - log:带 offset 的单行(stream=stdout/stderr);
 * - status:执行状态迁移(running 等);
 * - heartbeat:服务端保活(server_time);
 * - end:收尾(final_offset + 终态 status)。
 *
 * type 别名(非 interface):实时帧 payload 为 Record<string, unknown>,
 * 别名具隐式索引签名,`payload as LogFrame` 断言方可编译。
 */
export type LogFrame = {
  readonly type: 'log' | 'status' | 'heartbeat' | 'end';
  readonly stream?: string;
  readonly offset?: number;
  readonly line?: string;
  readonly status?: string;
  readonly final_offset?: number;
  readonly server_time?: string;
};

/** 凭证元信息(§2.2 runtime_credentials;encrypted_value 只进不出,UI 值恒 ***）。 */
export interface CredentialMeta {
  readonly id: string;
  readonly name: string;
  readonly kind: 'env' | 'file' | 'repo_token' | 'ssh_key';
}

/** runtime 状态展示序(列表筛选下拉,§4.1)。 */
export const RUNTIME_STATUS_ORDER: readonly RuntimeStatus[] = [
  'pending',
  'online',
  'unavailable',
  'paused',
  'draining',
  'decommissioned',
];

export const RUNTIME_KIND_ORDER: readonly RuntimeKind[] = ['platform_managed', 'self_hosted'];

/** 逻辑执行终态(§4.7):到达即不再迁移,UI 呈现绿 / 红状态条。 */
export const TERMINAL_EXECUTION_STATUSES: ReadonlySet<ExecutionStatus> = new Set<ExecutionStatus>([
  'completed',
  'failed',
  'timeout',
  'cancelled',
]);

/** 成功类终态(绿色状态条);其余终态为失败类(红色,附 failure_reason)。 */
export const SUCCESS_EXECUTION_STATUSES: ReadonlySet<ExecutionStatus> = new Set<ExecutionStatus>([
  'completed',
]);

/**
 * 审批项(契约层,README §6.10 统一审批 inbox;`GET /workspaces/{ws}/approvals`)。
 * 字段镜像后端 `_approval_response`:`role=mine` 即「待我审批」= status pending。
 */
export interface ApprovalSummary {
  readonly id: string;
  readonly subject_type: string;
  readonly subject_execution_id: string | null;
  readonly subject_task_id: string | null;
  readonly status: string;
  /** 人类可读的动作摘要(后端预渲染,直接呈现)。 */
  readonly action_summary: string;
  readonly requested_at: string;
  readonly expires_at: string;
  readonly decided_at: string | null;
  readonly decision_comment: string | null;
  readonly execution_status: string | null;
}
