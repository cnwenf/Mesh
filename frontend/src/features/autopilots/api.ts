/**
 * Autopilot 控制台 API 调用(契约层,autopilot.md §3.1 / README §6.14 包络)。
 *
 * 规则 CRUD / 启停 / test-run / 运行历史 / kill switch / webhook 凭据。
 * 入站 webhook 端点(`/api/v1/webhooks/inbound/{token}`)为外部系统使用
 * (HMAC 签名鉴权,裸 JSON 契约),不经 UI 调用;此处仅导出 URL 拼装供
 * Webhook 配置页展示。
 */
import type { MeshApiClient } from '../../api';
import { env } from '../../env';
import type {
  AutopilotRule,
  AutopilotRun,
  KillSwitchResult,
  RunArtifact,
  SchedulePreview,
  TestRunResult,
  WebhookEventItem,
  WebhookSecretCreated,
  WebhookSecretPublic,
} from './types';

const workspaceAutopilotsPath = (workspaceId: string): string =>
  `/api/v1/workspaces/${workspaceId}/autopilots`;

const autopilotPath = (workspaceId: string, autopilotId: string): string =>
  `${workspaceAutopilotsPath(workspaceId)}/${autopilotId}`;

const runsPath = (workspaceId: string): string =>
  `/api/v1/workspaces/${workspaceId}/autopilot-runs`;

const runPath = (workspaceId: string, runId: string): string => `${runsPath(workspaceId)}/${runId}`;

const secretsPath = (workspaceId: string): string =>
  `/api/v1/workspaces/${workspaceId}/webhook-secrets`;

/** 实时频道(autopilot.md §3.5,README §6.7)。 */
export const workspaceAutopilotsChannel = (workspaceId: string): string =>
  `workspace:${workspaceId}:autopilots`;

export const autopilotChannel = (autopilotId: string): string => `autopilot:${autopilotId}`;

/** 入站 webhook URL 展示(配置页只展示,不调用;§3.2 HMAC 端点)。 */
export const inboundWebhookUrl = (token: string): string => {
  const base = env.apiBaseUrl || window.location.origin;
  return `${base}/api/v1/webhooks/inbound/${token}`;
};

export interface ListAutopilotsParams {
  readonly status?: string;
  readonly trigger_type?: string;
  readonly search?: string;
  readonly cursor?: string;
  readonly limit?: number;
}

export async function listAutopilots(
  client: MeshApiClient,
  workspaceId: string,
  params: ListAutopilotsParams = {},
): Promise<{ data: AutopilotRule[]; nextCursor: string | null }> {
  const envelope = await client.list<AutopilotRule>(workspaceAutopilotsPath(workspaceId), {
    query: {
      status: params.status,
      trigger_type: params.trigger_type,
      search: params.search,
      cursor: params.cursor,
      limit: params.limit,
    },
  });
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

export async function getAutopilot(
  client: MeshApiClient,
  workspaceId: string,
  autopilotId: string,
): Promise<AutopilotRule> {
  return client.request<AutopilotRule>('GET', autopilotPath(workspaceId, autopilotId));
}

export type CreateAutopilotBody = Readonly<Record<string, unknown>>;

export async function createAutopilot(
  client: MeshApiClient,
  workspaceId: string,
  body: CreateAutopilotBody,
): Promise<AutopilotRule> {
  return client.request<AutopilotRule>('POST', workspaceAutopilotsPath(workspaceId), { body });
}

export async function patchAutopilot(
  client: MeshApiClient,
  workspaceId: string,
  autopilotId: string,
  body: CreateAutopilotBody,
): Promise<AutopilotRule> {
  return client.request<AutopilotRule>('PATCH', autopilotPath(workspaceId, autopilotId), { body });
}

export async function deleteAutopilot(
  client: MeshApiClient,
  workspaceId: string,
  autopilotId: string,
): Promise<void> {
  await client.request<null>('DELETE', autopilotPath(workspaceId, autopilotId));
}

export async function pauseAutopilot(
  client: MeshApiClient,
  workspaceId: string,
  autopilotId: string,
): Promise<AutopilotRule> {
  return client.request<AutopilotRule>('POST', `${autopilotPath(workspaceId, autopilotId)}/pause`, {
    body: {},
  });
}

export async function resumeAutopilot(
  client: MeshApiClient,
  workspaceId: string,
  autopilotId: string,
): Promise<AutopilotRule> {
  return client.request<AutopilotRule>('POST', `${autopilotPath(workspaceId, autopilotId)}/resume`, {
    body: {},
  });
}

export async function previewSchedule(
  client: MeshApiClient,
  workspaceId: string,
  autopilotId: string,
  count = 5,
): Promise<SchedulePreview> {
  return client.request<SchedulePreview>(
    'GET',
    `${autopilotPath(workspaceId, autopilotId)}/preview-schedule`,
    { query: { count } },
  );
}

export async function testRunAutopilot(
  client: MeshApiClient,
  workspaceId: string,
  autopilotId: string,
  body: { readonly simulate_trigger_payload?: Record<string, unknown>; readonly dry_run?: boolean },
): Promise<TestRunResult> {
  return client.request<TestRunResult>(
    'POST',
    `${autopilotPath(workspaceId, autopilotId)}/test-run`,
    { body },
  );
}

export async function listAutopilotRuns(
  client: MeshApiClient,
  workspaceId: string,
  autopilotId: string,
  params: { readonly status?: string; readonly cursor?: string; readonly limit?: number } = {},
): Promise<{ data: AutopilotRun[]; nextCursor: string | null }> {
  const envelope = await client.list<AutopilotRun>(
    `${autopilotPath(workspaceId, autopilotId)}/runs`,
    { query: { status: params.status, cursor: params.cursor, limit: params.limit } },
  );
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

export async function getAutopilotRun(
  client: MeshApiClient,
  workspaceId: string,
  runId: string,
): Promise<AutopilotRun> {
  return client.request<AutopilotRun>('GET', runPath(workspaceId, runId));
}

export async function listRunArtifacts(
  client: MeshApiClient,
  workspaceId: string,
  runId: string,
): Promise<RunArtifact[]> {
  const envelope = await client.list<RunArtifact>(`${runPath(workspaceId, runId)}/artifacts`);
  return envelope.data;
}

export async function cancelRun(
  client: MeshApiClient,
  workspaceId: string,
  runId: string,
): Promise<AutopilotRun> {
  return client.request<AutopilotRun>('POST', `${runPath(workspaceId, runId)}/cancel`, { body: {} });
}

/** 审批薄封装(README §6.10):转发统一 approvals 决策,收口于本端点。 */
export async function approveRun(
  client: MeshApiClient,
  workspaceId: string,
  runId: string,
  comment?: string,
): Promise<Record<string, unknown>> {
  return client.request<Record<string, unknown>>('POST', `${runPath(workspaceId, runId)}/approve`, {
    body: comment ? { comment } : {},
  });
}

export async function rejectRun(
  client: MeshApiClient,
  workspaceId: string,
  runId: string,
  comment?: string,
): Promise<Record<string, unknown>> {
  return client.request<Record<string, unknown>>('POST', `${runPath(workspaceId, runId)}/reject`, {
    body: comment ? { comment } : {},
  });
}

export async function getKillSwitchState(
  client: MeshApiClient,
  workspaceId: string,
): Promise<{ kill_switch: boolean }> {
  return client.request<{ kill_switch: boolean }>(
    'GET',
    `${workspaceAutopilotsPath(workspaceId)}/kill-switch`,
  );
}

export async function setKillSwitch(
  client: MeshApiClient,
  workspaceId: string,
  body: { readonly enabled: boolean; readonly reason?: string },
): Promise<KillSwitchResult> {
  return client.request<KillSwitchResult>(
    'POST',
    `${workspaceAutopilotsPath(workspaceId)}/kill-switch`,
    { body },
  );
}

export async function listWebhookSecrets(
  client: MeshApiClient,
  workspaceId: string,
): Promise<WebhookSecretPublic[]> {
  const envelope = await client.list<WebhookSecretPublic>(secretsPath(workspaceId));
  return envelope.data;
}

export async function createWebhookSecret(
  client: MeshApiClient,
  workspaceId: string,
  label: string,
): Promise<WebhookSecretCreated> {
  return client.request<WebhookSecretCreated>('POST', secretsPath(workspaceId), {
    body: { label },
  });
}

export async function rotateWebhookSecret(
  client: MeshApiClient,
  workspaceId: string,
  secretId: string,
): Promise<WebhookSecretCreated> {
  return client.request<WebhookSecretCreated>(
    'POST',
    `${secretsPath(workspaceId)}/${secretId}/rotate`,
    { body: {} },
  );
}

/** Stateless cron preview (autopilot.md §4.2 live preview; usable before a rule exists). */
export async function previewScheduleParams(
  client: MeshApiClient,
  workspaceId: string,
  params: { cron: string; timezone: string; count?: number },
): Promise<SchedulePreview> {
  return client.request<SchedulePreview>(
    'POST',
    `/api/v1/workspaces/${workspaceId}/autopilots/preview-schedule`,
    { body: params },
  );
}

export interface ListWebhookEventsParams {
  readonly autopilotId?: string;
  readonly processStatus?: string;
  readonly cursor?: string;
  readonly limit?: number;
}

/** Inbound event audit trail (autopilot.md §4.1 最近事件). */
export async function listWebhookEvents(
  client: MeshApiClient,
  workspaceId: string,
  params: ListWebhookEventsParams = {},
): Promise<{ data: WebhookEventItem[]; nextCursor: string | null }> {
  const envelope = await client.list<WebhookEventItem>(
    `/api/v1/workspaces/${workspaceId}/webhook-events`,
    {
      query: {
        autopilot_id: params.autopilotId,
        process_status: params.processStatus,
        cursor: params.cursor,
        limit: params.limit,
      },
    },
  );
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}
