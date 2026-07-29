/**
 * 集成平台实体类型(integrations.md §2 / §3.1)。
 * 时间一律 UTC RFC3339 字符串;id 一律 UUID 字符串。
 *
 * - `Integration`:连接器实例(kind 决定适配点);`config` 仅非密配置,密钥以 `*_ref`
 *   指向加密密文(§2.7 / README §6.16),响应面以 `has_secret` 布尔表达凭据存在,
 *   **永不回显明文**。
 * - `Binding`:外部身份 ↔ 工作区/项目绑定;`(provider, provider_tenant_key, external_ref)`
 *   全局唯一(§2.3)。
 * - `IntegrationEvent`:入站台账(签名 + 去重 + 审计;载荷按不可信数据隔离,§6.15)。
 * - `WebhookSubscription` / `Delivery`:出向订阅 + 投递台账(HMAC 签名 / 重试退避 / 熔断)。
 * - `ExternalIdentity`:全局身份映射(外部平台账号 ↔ Mesh `users.id`,§2.4.1)。
 * - `VcsLink`:VCS 对象 ↔ issue 关联真源(§2.8 / §3.3)。
 */

export type IntegrationKind =
  | 'im_feishu'
  | 'im_slack'
  | 'vcs_github'
  | 'vcs_gitlab'
  | 'webhook_outbound';

export const INTEGRATION_KINDS: ReadonlyArray<IntegrationKind> = [
  'im_feishu',
  'im_slack',
  'vcs_github',
  'vcs_gitlab',
  'webhook_outbound',
];

/** 集成启用状态(§2.2;`disabled` 时入站拒绝分发、出站停发)。 */
export type IntegrationStatus = 'active' | 'disabled';

/** 连接器健康度(§4.1 列表徽章;凭据失效联动「重新授权」CTA)。 */
export type IntegrationHealthState = 'unknown' | 'healthy' | 'auth_failed' | 'unreachable';

export const INTEGRATION_HEALTH_STATES: ReadonlyArray<IntegrationHealthState> = [
  'unknown',
  'healthy',
  'auth_failed',
  'unreachable',
];

/** 支持 OAuth 授权流(整页跳外部平台)的 kind;webhook_outbound 为手填凭据。 */
export const OAUTH_KINDS: ReadonlySet<IntegrationKind> = new Set<IntegrationKind>([
  'im_feishu',
  'im_slack',
  'vcs_github',
  'vcs_gitlab',
]);

export interface Integration {
  readonly id: string;
  readonly workspace_id: string;
  readonly kind: IntegrationKind;
  readonly name: string;
  readonly status: IntegrationStatus;
  /** 非密平台配置(§2.7;严禁明文 secret)。 */
  readonly config: Readonly<Record<string, unknown>>;
  /** 凭据存在标记(凭据密文引用 `secret_ref` 的回显面,绝不暴露明文)。 */
  readonly has_secret: boolean;
  /** 连接器健康度(列表徽章 + 详情;`auth_failed` 触发重新授权 CTA)。 */
  readonly health_state: IntegrationHealthState;
  /** 最近一次健康检查的错误信息(展示为 tooltip/副文本,不回显凭据)。 */
  readonly last_error: string | null;
  /** 最近一次成功连通时间(UTC RFC3339)。 */
  readonly last_success_at: string | null;
  /** 近 7 天入站事件量(§4.1 列表列)。 */
  readonly events_7d: number;
  readonly created_by: string;
  readonly created_at: string;
  readonly updated_at: string;
}

/** 规范化提供商标识(§2.3,从 `integrations.kind` 归一)。 */
export type BindingProvider = 'feishu' | 'slack' | 'github' | 'gitlab' | 'webhook';

export type BindingScope = 'workspace' | 'project';

export type BindingStatus = 'active' | 'disabled';

/** 匹配规则(§2.7:字段间 AND,同类多值 OR)。 */
export interface MatchConfig {
  readonly trigger_on?: ReadonlyArray<'mention' | 'direct_message' | 'keyword'>;
  readonly mention_agents?: ReadonlyArray<string>;
  readonly keyword_include?: ReadonlyArray<string>;
  readonly keyword_exclude?: ReadonlyArray<string>;
  readonly branch_pattern?: string;
  readonly vcs_events?: ReadonlyArray<string>;
  readonly auto_status_map?: Readonly<Record<string, string>>;
}

export interface Binding {
  readonly id: string;
  readonly integration_id: string;
  readonly provider: BindingProvider;
  readonly provider_tenant_key: string;
  readonly scope: BindingScope;
  readonly project_id: string | null;
  readonly external_ref: string;
  readonly match_config: MatchConfig;
  readonly bound_agent_id: string | null;
  readonly status: BindingStatus;
  readonly created_at: string;
  readonly updated_at: string;
}

export type SignatureStatus = 'valid' | 'invalid' | 'missing';

export type ProcessStatus =
  | 'received'
  | 'matched'
  | 'dispatched'
  | 'deduped'
  | 'rejected'
  | 'processed'
  | 'failed';

export interface IntegrationEvent {
  readonly id: string;
  readonly integration_id: string;
  readonly external_event_id: string;
  readonly event_type: string;
  readonly payload: Readonly<Record<string, unknown>>;
  readonly signature_status: SignatureStatus;
  readonly process_status: ProcessStatus;
  readonly received_at: string;
}

export type SubscriptionStatus = 'active' | 'paused' | 'disabled';

export interface WebhookSubscription {
  readonly id: string;
  readonly integration_id: string | null;
  readonly url: string;
  readonly event_types: ReadonlyArray<string>;
  readonly status: SubscriptionStatus;
  readonly fail_count: number;
  readonly has_secret: boolean;
  /** 投递台账统计(§4.1 列表成功率:total=0 时 success_rate=null)。 */
  readonly deliveries_total: number;
  readonly deliveries_sent: number;
  readonly success_rate: number | null;
  /** 仅创建响应出现一次的签名密钥明文(§3.1;随后永不回显)。 */
  readonly secret?: string;
  readonly created_by: string;
  readonly created_at: string;
  readonly updated_at: string;
}

export type DeliveryState = 'pending' | 'sent' | 'failed';

export interface Delivery {
  readonly id: string;
  readonly subscription_id: string;
  readonly event_ref: string;
  readonly state: DeliveryState;
  readonly attempts: number;
  readonly next_retry_at: string | null;
  readonly response_status: number | null;
  readonly last_error: string | null;
  readonly created_at: string;
}

export type IdentityProvider = 'feishu' | 'slack' | 'github' | 'gitlab';

export interface ExternalIdentity {
  readonly id: string;
  readonly provider: IdentityProvider;
  readonly provider_tenant_key: string;
  readonly external_user_key: string;
  readonly user_id: string;
  readonly created_in_workspace_id: string | null;
  readonly verified_at: string;
  readonly created_at: string;
}

export type VcsLinkStatus = 'active' | 'stale' | 'deleted';

export type VcsObjectType = 'pull_request' | 'commit' | 'branch' | 'repository';

export const VCS_OBJECT_TYPES: ReadonlyArray<VcsObjectType> = [
  'pull_request',
  'commit',
  'branch',
  'repository',
];

export interface VcsLink {
  readonly id: string;
  readonly integration_id: string;
  readonly provider: BindingProvider;
  readonly external_object_type: VcsObjectType;
  readonly external_object_ref: string;
  /** 外部对象 Web URL(深链;不可推导时为 null,渲染回退纯文本,§4.2)。 */
  readonly url: string | null;
  readonly mesh_entity_type: 'issue';
  readonly mesh_entity_id: string;
  readonly link_source: string;
  readonly status: VcsLinkStatus;
  readonly external_state: Readonly<Record<string, unknown>> | null;
  readonly created_by: string | null;
  readonly created_at: string;
}

/** 创建集成响应(§3.1):集成对象 + 凭据受理标记。 */
export interface CreateIntegrationResult {
  readonly integration: Integration;
  readonly secret_accepted: boolean;
}

/** 连接器目录卡片的展示元数据(§4.2:图标 + 名称 key + 能力标签)。 */
export interface ConnectorMeta {
  readonly kind: IntegrationKind;
  readonly icon: string;
  readonly nameKey: string;
  readonly capabilityKeys: ReadonlyArray<string>;
}

export const CONNECTOR_CATALOG: ReadonlyArray<ConnectorMeta> = [
  {
    kind: 'im_feishu',
    icon: '🐦',
    nameKey: 'integrations.kind.im_feishu',
    capabilityKeys: [
      'integrations.capability.im_notify',
      'integrations.capability.approval_card',
      'integrations.capability.event_trigger',
    ],
  },
  {
    kind: 'im_slack',
    icon: '💬',
    nameKey: 'integrations.kind.im_slack',
    capabilityKeys: [
      'integrations.capability.im_notify',
      'integrations.capability.approval_card',
      'integrations.capability.event_trigger',
    ],
  },
  {
    kind: 'vcs_github',
    icon: '🐙',
    nameKey: 'integrations.kind.vcs_github',
    capabilityKeys: ['integrations.capability.vcs_link', 'integrations.capability.status_flow'],
  },
  {
    kind: 'vcs_gitlab',
    icon: '🦊',
    nameKey: 'integrations.kind.vcs_gitlab',
    capabilityKeys: ['integrations.capability.vcs_link', 'integrations.capability.status_flow'],
  },
  {
    kind: 'webhook_outbound',
    icon: '📤',
    nameKey: 'integrations.kind.webhook_outbound',
    capabilityKeys: ['integrations.capability.outbound'],
  },
];
