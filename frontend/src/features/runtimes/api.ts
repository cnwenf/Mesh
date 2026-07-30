/**
 * Runtime / Execution 控制台 API 调用(契约层,runtime.md §3.1 / README §6.14 包络)。
 *
 * 仅覆盖控制台 API(用户会话凭证);守护进程用的机器 API(`/api/v1/daemon/*`,
 * runtime token 鉴权)不经 UI 调用。后端把控制台路由挂载为 workspace 作用域,
 * 故全部路径形如 `/api/v1/workspaces/{ws}/runtimes/...`(与 agents / members 同构)。
 *
 * 包络:`client.request<T>` 解 `{"data":...}`;`client.list<T>` 原样返回
 * `{data, next_cursor}`;凭证明文只在 createCredential 请求体出现,任何响应
 * 都不回显(§2.2 红线)。
 */
import type { MeshApiClient } from '../../api';
import { env } from '../../env';
import type {
  ApprovalSummary,
  AttemptSummary,
  CredentialMeta,
  ExecutionDetail,
  ExecutionLogPage,
  ExecutionSummary,
  RuntimeDetail,
  RuntimeKind,
  RuntimeStatus,
  RuntimeWithActivation,
  RotateTokenResult,
} from './types';

const workspaceRuntimesPath = (workspaceId: string): string =>
  `/api/v1/workspaces/${workspaceId}/runtimes`;

const runtimePath = (workspaceId: string, runtimeId: string): string =>
  `${workspaceRuntimesPath(workspaceId)}/${runtimeId}`;

const workspaceExecutionsPath = (workspaceId: string): string =>
  `/api/v1/workspaces/${workspaceId}/executions`;

const workspaceApprovalsPath = (workspaceId: string): string =>
  `/api/v1/workspaces/${workspaceId}/approvals`;

const executionPath = (workspaceId: string, executionId: string): string =>
  `${workspaceExecutionsPath(workspaceId)}/${executionId}`;

const workspaceCredentialsPath = (workspaceId: string): string =>
  `/api/v1/workspaces/${workspaceId}/credentials`;

const credentialPath = (workspaceId: string, credentialId: string): string =>
  `${workspaceCredentialsPath(workspaceId)}/${credentialId}`;

/** 实时频道(runtime.md §3.6,README §6.7:<entity>.<action> + 频道内 seq)。 */
export const workspaceRuntimesChannel = (workspaceId: string): string =>
  `workspace:${workspaceId}:runtimes`;

export const workspaceExecutionsChannel = (workspaceId: string): string =>
  `workspace:${workspaceId}:executions`;

export const executionChannel = (executionId: string): string => `execution:${executionId}`;

/** 日志流主通道(WS);帧 event='execution.log',payload 为 §3.3 日志帧。 */
export const executionLogsChannel = (executionId: string): string =>
  `execution:${executionId}:logs`;

export const workspaceQueueChannel = (workspaceId: string): string =>
  `workspace:${workspaceId}:queue`;

export interface ListRuntimesParams {
  readonly status?: RuntimeStatus;
  readonly kind?: RuntimeKind;
  readonly cursor?: string;
  readonly limit?: number;
}

export async function listRuntimes(
  client: MeshApiClient,
  workspaceId: string,
  params: ListRuntimesParams = {},
): Promise<{ data: RuntimeDetail[]; nextCursor: string | null }> {
  const envelope = await client.list<RuntimeDetail>(workspaceRuntimesPath(workspaceId), {
    query: {
      status: params.status,
      kind: params.kind,
      cursor: params.cursor,
      limit: params.limit,
    },
  });
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

export interface CreateRuntimeBody {
  readonly name: string;
  readonly kind: RuntimeKind;
  readonly labels?: Readonly<Record<string, string>>;
  readonly max_concurrent?: number;
}

/** 创建 runtime(§4.3):建 pending 影子记录,返回一次性激活码 + 签名发布包安装信息。 */
export async function createRuntime(
  client: MeshApiClient,
  workspaceId: string,
  body: CreateRuntimeBody,
): Promise<RuntimeWithActivation> {
  return client.request<RuntimeWithActivation>('POST', workspaceRuntimesPath(workspaceId), {
    body,
  });
}

export async function getRuntime(
  client: MeshApiClient,
  workspaceId: string,
  runtimeId: string,
): Promise<RuntimeDetail> {
  return client.request<RuntimeDetail>('GET', runtimePath(workspaceId, runtimeId));
}

export interface PatchRuntimeBody {
  readonly name?: string;
  readonly labels?: Readonly<Record<string, string>>;
  readonly max_concurrent?: number;
}

export async function patchRuntime(
  client: MeshApiClient,
  workspaceId: string,
  runtimeId: string,
  body: PatchRuntimeBody,
): Promise<RuntimeDetail> {
  return client.request<RuntimeDetail>('PATCH', runtimePath(workspaceId, runtimeId), { body });
}

/** 暂停(不再领新任务,§4.10 人类干预点)。 */
export async function pauseRuntime(
  client: MeshApiClient,
  workspaceId: string,
  runtimeId: string,
): Promise<RuntimeDetail> {
  return client.request<RuntimeDetail>('POST', `${runtimePath(workspaceId, runtimeId)}:pause`, {
    body: {},
  });
}

export async function resumeRuntime(
  client: MeshApiClient,
  workspaceId: string,
  runtimeId: string,
): Promise<RuntimeDetail> {
  return client.request<RuntimeDetail>('POST', `${runtimePath(workspaceId, runtimeId)}:resume`, {
    body: {},
  });
}

/** 轮换 runtime API token(§3.1;新 token 明文仅此一次返回,UI 以弹窗呈现)。 */
export async function rotateRuntimeToken(
  client: MeshApiClient,
  workspaceId: string,
  runtimeId: string,
): Promise<RotateTokenResult> {
  return client.request<RotateTokenResult>(
    'POST',
    `${runtimePath(workspaceId, runtimeId)}/tokens:rotate`,
    { body: {} },
  );
}

export async function deleteRuntime(
  client: MeshApiClient,
  workspaceId: string,
  runtimeId: string,
): Promise<void> {
  await client.request<void>('DELETE', runtimePath(workspaceId, runtimeId));
}

/** 该 runtime 的执行历史(§4.2 详情页下半部)。 */
export async function listRuntimeExecutions(
  client: MeshApiClient,
  workspaceId: string,
  runtimeId: string,
  params: { readonly cursor?: string; readonly limit?: number } = {},
): Promise<{ data: ExecutionDetail[]; nextCursor: string | null }> {
  const envelope = await client.list<ExecutionDetail>(
    `${runtimePath(workspaceId, runtimeId)}/executions`,
    { query: { cursor: params.cursor, limit: params.limit } },
  );
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

/**
 * 工作区级执行列表(契约层,design-quality §3.2 首页「AI 运行」块)。
 * 后端 `GET /workspaces/{ws}/executions` 支持 status/agent_id/issue_id 过滤 + 游标分页;
 * 首页取最近执行后按活跃/需关注态过滤渲染。
 */
export async function listWorkspaceExecutions(
  client: MeshApiClient,
  workspaceId: string,
  params: {
    readonly status?: string;
    readonly agent_id?: string;
    readonly issue_id?: string;
    readonly cursor?: string;
    readonly limit?: number;
  } = {},
): Promise<{ data: ExecutionSummary[]; nextCursor: string | null }> {
  const envelope = await client.list<ExecutionSummary>(workspaceExecutionsPath(workspaceId), {
    query: {
      status: params.status,
      agent_id: params.agent_id,
      issue_id: params.issue_id,
      cursor: params.cursor,
      limit: params.limit,
    },
  });
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

/**
 * 工作区级审批列表(契约层,design-quality §3.2 首页「等待确认」块)。
 * 后端 `GET /workspaces/{ws}/approvals?role=mine` = F9「待我审批」统一 inbox(pending)。
 */
export async function listWorkspaceApprovals(
  client: MeshApiClient,
  workspaceId: string,
  params: { readonly role?: string; readonly status?: string } = {},
): Promise<{ data: ApprovalSummary[]; nextCursor: string | null }> {
  const envelope = await client.list<ApprovalSummary>(workspaceApprovalsPath(workspaceId), {
    query: { role: params.role, status: params.status },
  });
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

export async function getExecution(
  client: MeshApiClient,
  workspaceId: string,
  executionId: string,
): Promise<ExecutionDetail> {
  return client.request<ExecutionDetail>('GET', executionPath(workspaceId, executionId));
}

/** 取消执行(§4.7 两段式:cancelling 中间态 → cancelled;幂等)。 */
export async function cancelExecution(
  client: MeshApiClient,
  workspaceId: string,
  executionId: string,
): Promise<ExecutionDetail> {
  return client.request<ExecutionDetail>(
    'POST',
    `${executionPath(workspaceId, executionId)}:cancel`,
    { body: {} },
  );
}

/** 冻结可疑执行(§4.10:立即吊销短期凭证 envelope、保留现场)。 */
export async function freezeExecution(
  client: MeshApiClient,
  workspaceId: string,
  executionId: string,
): Promise<ExecutionDetail> {
  return client.request<ExecutionDetail>(
    'POST',
    `${executionPath(workspaceId, executionId)}:freeze`,
    { body: {} },
  );
}

/**
 * 拉取日志(§3.1 REST 补历史 / 续传):`?offset=N` 从累计字节偏移 N 起读;
 * 响应包络 `{"data": {"lines": [...], "next_offset": M}}`,经 request 解包后
 * 返回内层 `{lines, next_offset}`。
 */
export async function listExecutionLogs(
  client: MeshApiClient,
  workspaceId: string,
  executionId: string,
  params: { readonly offset?: number; readonly stream?: string } = {},
): Promise<ExecutionLogPage> {
  return client.request<ExecutionLogPage>(
    'GET',
    `${executionPath(workspaceId, executionId)}/logs`,
    {
      query: { offset: params.offset, stream: params.stream },
    },
  );
}

/**
 * SSE 降级通道 URL(§3.3 / §4.9):实时态未连通时以 EventSource 订阅同一 offset
 * 协议的 JSON 行帧。绝对 URL = apiBaseUrl + 路径(同源部署 apiBaseUrl 可为空)。
 */
export function executionLogsStreamUrl(
  workspaceId: string,
  executionId: string,
  offset: number,
): string {
  return `${env.apiBaseUrl}${executionPath(workspaceId, executionId)}/logs/stream?offset=${offset}`;
}

export async function listCredentials(
  client: MeshApiClient,
  workspaceId: string,
): Promise<CredentialMeta[]> {
  const envelope = await client.list<CredentialMeta>(workspaceCredentialsPath(workspaceId));
  return envelope.data;
}

export interface CreateCredentialBody {
  readonly name: string;
  readonly kind: CredentialMeta['kind'];
  /** 明文仅随请求体一次性上行(§2.2 红线:服务端只存密文,永不回显)。 */
  readonly value: string;
  readonly scope?: string;
}

export async function createCredential(
  client: MeshApiClient,
  workspaceId: string,
  body: CreateCredentialBody,
): Promise<CredentialMeta> {
  return client.request<CredentialMeta>('POST', workspaceCredentialsPath(workspaceId), { body });
}

export async function deleteCredential(
  client: MeshApiClient,
  workspaceId: string,
  credentialId: string,
): Promise<void> {
  await client.request<void>('DELETE', credentialPath(workspaceId, credentialId));
}

/** 执行详情中最新在途 / 末次尝试(§4.4:日志偏移 / 分支取当前 attempt)。 */
export function latestAttempt(execution: ExecutionDetail): AttemptSummary | null {
  if (execution.attempts.length === 0) return null;
  return execution.attempts[execution.attempts.length - 1];
}
