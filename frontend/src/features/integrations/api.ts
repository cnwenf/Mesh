/**
 * 集成平台 API 调用(契约层,integrations.md §3 / README §6.14 包络)。
 *
 * 管理 CRUD / 绑定 / 入站事件台账 / 出向订阅 + 投递 / 外部身份建链解链 /
 * VCS 关联。入站回调端点(`/api/v1/integrations/{platform}/events`)为外部平台
 * 使用(平台签名鉴权,裸 JSON 契约),不经 UI 调用。OAuth 授权端点返回 302,
 * 经 `window.location` 整页跳转(非 fetch);此处仅导出 URL 拼装。
 */
import type { MeshApiClient } from '../../api';
import { env } from '../../env';
import type {
  Binding,
  CreateIntegrationResult,
  Delivery,
  ExternalIdentity,
  Integration,
  IntegrationEvent,
  IntegrationKind,
  MatchConfig,
  VcsLink,
  VcsObjectType,
  WebhookSubscription,
} from './types';

const workspaceIntegrationsPath = (workspaceId: string): string =>
  `/api/v1/workspaces/${workspaceId}/integrations`;

const integrationPath = (workspaceId: string, integrationId: string): string =>
  `${workspaceIntegrationsPath(workspaceId)}/${integrationId}`;

const integrationBindingsPath = (workspaceId: string, integrationId: string): string =>
  `${integrationPath(workspaceId, integrationId)}/bindings`;

const bindingPath = (workspaceId: string, bindingId: string): string =>
  `/api/v1/workspaces/${workspaceId}/integration-bindings/${bindingId}`;

const integrationEventsPath = (workspaceId: string, integrationId: string): string =>
  `${integrationPath(workspaceId, integrationId)}/events`;

const subscriptionsPath = (workspaceId: string): string =>
  `/api/v1/workspaces/${workspaceId}/webhook-subscriptions`;

const subscriptionPath = (workspaceId: string, subscriptionId: string): string =>
  `${subscriptionsPath(workspaceId)}/${subscriptionId}`;

const externalIdentitiesPath = (workspaceId: string): string =>
  `/api/v1/workspaces/${workspaceId}/external-identities`;

/** 实时频道(integrations.md §3.6,README §6.7)。 */
export const workspaceIntegrationsChannel = (workspaceId: string): string =>
  `workspace:${workspaceId}:integrations`;

export const integrationChannel = (integrationId: string): string => `integration:${integrationId}`;

/** OAuth 授权跳转 URL(§3.1:302 跳外部平台,经 window.location 整页跳转,非 fetch)。 */
export const integrationAuthorizeUrl = (workspaceId: string, kind: IntegrationKind): string => {
  const base = env.apiBaseUrl || window.location.origin;
  return `${base}/api/v1/workspaces/${workspaceId}/integrations/oauth/${kind}/authorize`;
};

export interface ListIntegrationsParams {
  readonly kind?: string;
  readonly status?: string;
  readonly cursor?: string;
  readonly limit?: number;
}

export async function listIntegrations(
  client: MeshApiClient,
  workspaceId: string,
  params: ListIntegrationsParams = {},
): Promise<{ data: Integration[]; nextCursor: string | null }> {
  const envelope = await client.list<Integration>(workspaceIntegrationsPath(workspaceId), {
    query: {
      kind: params.kind,
      status: params.status,
      cursor: params.cursor,
      limit: params.limit,
    },
  });
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

export interface CreateIntegrationBody {
  readonly kind: IntegrationKind;
  readonly name: string;
  readonly config?: Readonly<Record<string, unknown>>;
  readonly secret?: string;
}

export async function createIntegration(
  client: MeshApiClient,
  workspaceId: string,
  body: CreateIntegrationBody,
): Promise<CreateIntegrationResult> {
  return client.request<CreateIntegrationResult>('POST', workspaceIntegrationsPath(workspaceId), {
    body,
  });
}

export async function getIntegration(
  client: MeshApiClient,
  workspaceId: string,
  integrationId: string,
): Promise<Integration> {
  return client.request<Integration>('GET', integrationPath(workspaceId, integrationId));
}

export interface PatchIntegrationBody {
  readonly name?: string;
  readonly status?: 'active' | 'disabled';
  readonly config?: Readonly<Record<string, unknown>>;
}

export async function patchIntegration(
  client: MeshApiClient,
  workspaceId: string,
  integrationId: string,
  body: PatchIntegrationBody,
): Promise<Integration> {
  return client.request<Integration>('PATCH', integrationPath(workspaceId, integrationId), {
    body,
  });
}

export async function deleteIntegration(
  client: MeshApiClient,
  workspaceId: string,
  integrationId: string,
): Promise<void> {
  await client.request<null>('DELETE', integrationPath(workspaceId, integrationId));
}

export async function rotateIntegrationSecret(
  client: MeshApiClient,
  workspaceId: string,
  integrationId: string,
  secret: string,
): Promise<Integration> {
  return client.request<Integration>(
    'POST',
    `${integrationPath(workspaceId, integrationId)}/rotate-secret`,
    { body: { secret } },
  );
}

export async function listBindings(
  client: MeshApiClient,
  workspaceId: string,
  integrationId: string,
): Promise<{ data: Binding[]; nextCursor: string | null }> {
  const envelope = await client.list<Binding>(
    integrationBindingsPath(workspaceId, integrationId),
  );
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

export interface CreateBindingBody {
  readonly external_ref: string;
  readonly scope?: 'workspace' | 'project';
  readonly project_id?: string;
  readonly match_config?: MatchConfig;
  readonly bound_agent_id?: string | null;
}

export async function createBinding(
  client: MeshApiClient,
  workspaceId: string,
  integrationId: string,
  body: CreateBindingBody,
): Promise<Binding> {
  return client.request<Binding>('POST', integrationBindingsPath(workspaceId, integrationId), {
    body,
  });
}

export interface PatchBindingBody {
  readonly match_config?: MatchConfig;
  readonly bound_agent_id?: string | null;
  readonly clear_bound_agent?: boolean;
  readonly status?: 'active' | 'disabled';
}

export async function patchBinding(
  client: MeshApiClient,
  workspaceId: string,
  bindingId: string,
  body: PatchBindingBody,
): Promise<Binding> {
  return client.request<Binding>('PATCH', bindingPath(workspaceId, bindingId), { body });
}

export async function deleteBinding(
  client: MeshApiClient,
  workspaceId: string,
  bindingId: string,
): Promise<void> {
  await client.request<null>('DELETE', bindingPath(workspaceId, bindingId));
}

export interface ListIntegrationEventsParams {
  readonly signature_status?: string;
  readonly process_status?: string;
  readonly cursor?: string;
  readonly limit?: number;
}

export async function listIntegrationEvents(
  client: MeshApiClient,
  workspaceId: string,
  integrationId: string,
  params: ListIntegrationEventsParams = {},
): Promise<{ data: IntegrationEvent[]; nextCursor: string | null }> {
  const envelope = await client.list<IntegrationEvent>(
    integrationEventsPath(workspaceId, integrationId),
    {
      query: {
        signature_status: params.signature_status,
        process_status: params.process_status,
        cursor: params.cursor,
        limit: params.limit,
      },
    },
  );
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

export async function listSubscriptions(
  client: MeshApiClient,
  workspaceId: string,
): Promise<{ data: WebhookSubscription[]; nextCursor: string | null }> {
  const envelope = await client.list<WebhookSubscription>(subscriptionsPath(workspaceId));
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

export interface CreateSubscriptionBody {
  readonly url: string;
  readonly event_types?: ReadonlyArray<string>;
  readonly integration_id?: string;
}

/** 创建出向订阅(§3.1):201 响应携带仅显示一次的签名密钥 `secret`。 */
export async function createSubscription(
  client: MeshApiClient,
  workspaceId: string,
  body: CreateSubscriptionBody,
): Promise<WebhookSubscription> {
  return client.request<WebhookSubscription>('POST', subscriptionsPath(workspaceId), { body });
}

export async function getSubscription(
  client: MeshApiClient,
  workspaceId: string,
  subscriptionId: string,
): Promise<WebhookSubscription> {
  return client.request<WebhookSubscription>(
    'GET',
    subscriptionPath(workspaceId, subscriptionId),
  );
}

export interface PatchSubscriptionBody {
  readonly url?: string;
  readonly event_types?: ReadonlyArray<string>;
  readonly status?: 'active' | 'paused' | 'disabled';
}

export async function patchSubscription(
  client: MeshApiClient,
  workspaceId: string,
  subscriptionId: string,
  body: PatchSubscriptionBody,
): Promise<WebhookSubscription> {
  return client.request<WebhookSubscription>(
    'PATCH',
    subscriptionPath(workspaceId, subscriptionId),
    { body },
  );
}

export async function deleteSubscription(
  client: MeshApiClient,
  workspaceId: string,
  subscriptionId: string,
): Promise<void> {
  await client.request<null>('DELETE', subscriptionPath(workspaceId, subscriptionId));
}

/** 恢复熔断/暂停的订阅(§3.1:`fail_count` 清零)。 */
export async function resumeSubscription(
  client: MeshApiClient,
  workspaceId: string,
  subscriptionId: string,
): Promise<WebhookSubscription> {
  return client.request<WebhookSubscription>(
    'POST',
    `${subscriptionPath(workspaceId, subscriptionId)}/resume`,
    { body: {} },
  );
}

export interface ListDeliveriesParams {
  readonly state?: string;
  readonly limit?: number;
}

export async function listDeliveries(
  client: MeshApiClient,
  workspaceId: string,
  subscriptionId: string,
  params: ListDeliveriesParams = {},
): Promise<{ data: Delivery[]; nextCursor: string | null }> {
  const envelope = await client.list<Delivery>(
    `${subscriptionPath(workspaceId, subscriptionId)}/deliveries`,
    { query: { state: params.state, limit: params.limit } },
  );
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

/** 手动重试某条失败投递(§3.1)。 */
export async function retryDelivery(
  client: MeshApiClient,
  workspaceId: string,
  subscriptionId: string,
  deliveryId: string,
): Promise<Delivery> {
  return client.request<Delivery>(
    'POST',
    `${subscriptionPath(workspaceId, subscriptionId)}/deliveries/${deliveryId}/retry`,
    { body: {} },
  );
}

export async function listExternalIdentities(
  client: MeshApiClient,
  workspaceId: string,
): Promise<{ data: ExternalIdentity[]; nextCursor: string | null }> {
  const envelope = await client.list<ExternalIdentity>(externalIdentitiesPath(workspaceId));
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

export interface LinkIdentityBody {
  readonly provider: string;
  readonly integration_id: string;
  readonly external_user_key: string;
}

/** 建链(§3.1):向外部账号私聊下发一次性验证码。 */
export async function linkExternalIdentity(
  client: MeshApiClient,
  workspaceId: string,
  body: LinkIdentityBody,
): Promise<Record<string, unknown>> {
  return client.request<Record<string, unknown>>(
    'POST',
    `${externalIdentitiesPath(workspaceId)}:link`,
    { body },
  );
}

export interface ConfirmIdentityBody {
  readonly provider: string;
  readonly integration_id: string;
  readonly code: string;
}

/** 建链确认(§3.1):校验验证码后写入全局映射。 */
export async function confirmExternalIdentity(
  client: MeshApiClient,
  workspaceId: string,
  body: ConfirmIdentityBody,
): Promise<ExternalIdentity> {
  return client.request<ExternalIdentity>(
    'POST',
    `${externalIdentitiesPath(workspaceId)}:link-confirm`,
    { body },
  );
}

/** 解链(§3.1:仅映射所属 users.id 本人;无 admin 旁路,403 identity_unlink_forbidden)。 */
export async function unlinkExternalIdentity(
  client: MeshApiClient,
  workspaceId: string,
  identityId: string,
): Promise<void> {
  await client.request<null>('DELETE', `${externalIdentitiesPath(workspaceId)}/${identityId}`);
}

export interface VcsRef {
  readonly type: VcsObjectType;
  readonly url?: string;
  readonly id?: string;
}

export interface CreateVcsLinkBody {
  readonly integration_id: string;
  readonly vcs_ref: VcsRef;
  readonly mesh_entity_type: 'issue';
  readonly issue_id: string;
}

export async function createVcsLink(
  client: MeshApiClient,
  body: CreateVcsLinkBody,
): Promise<VcsLink> {
  return client.request<VcsLink>('POST', '/api/v1/integrations/vcs/links', { body });
}

export async function deleteVcsLink(client: MeshApiClient, linkId: string): Promise<void> {
  await client.request<null>('DELETE', `/api/v1/integrations/vcs/links/${linkId}`);
}

export async function listIssueVcsLinks(
  client: MeshApiClient,
  issueId: string,
): Promise<{ data: VcsLink[]; nextCursor: string | null }> {
  const envelope = await client.list<VcsLink>(`/api/v1/issues/${issueId}/vcs-links`);
  return { data: envelope.data, nextCursor: envelope.next_cursor };
}

export interface ResolveVcsBody {
  readonly integration_id: string;
  readonly source_text: string;
  readonly vcs_ref: VcsRef;
}

/** identifier 解析(§3.3):从文本/分支/PR 标题提取 `WEB-123` 自动关联。 */
export async function resolveVcsLink(
  client: MeshApiClient,
  body: ResolveVcsBody,
): Promise<VcsLink> {
  return client.request<VcsLink>('POST', '/api/v1/integrations/vcs/resolve', { body });
}
