/**
 * 集成平台展示层格式化(integrations.md §4 / README §6.18:本地化仅展示层)。
 * 纯函数 + 色调映射,无副作用,便于单测。
 */
import type { IconName, StatusDotTone } from '../../design';
import type {
  BindingStatus,
  DeliveryState,
  DingTalkStreamState,
  ExternalIdentity,
  IntegrationHealthState,
  IntegrationKind,
  IntegrationStatus,
  ProcessStatus,
  QueueItemState,
  SignatureStatus,
  SubscriptionStatus,
  VcsLinkStatus,
} from './types';
import { DINGTALK_STREAM_STATES, INTEGRATION_HEALTH_STATES } from './types';

/** 集成 kind 图标(§4.1 类型图标列;统一 SVG 图标名,经 design `<Icon>` 渲染,§7.1)。 */
export const KIND_ICON: Record<IntegrationKind, IconName> = {
  im_feishu: 'message',
  im_slack: 'chat',
  im_dingtalk: 'message',
  vcs_github: 'git-merge',
  vcs_gitlab: 'git-merge',
  webhook_outbound: 'upload',
};

export const INTEGRATION_STATUS_TONE: Record<IntegrationStatus, StatusDotTone> = {
  active: 'success',
  disabled: 'neutral',
};

/** 连接器健康度徽章色调(§4.1;unknown=中性 / healthy=成功 / auth_failed=危险 / unreachable=警告)。 */
export const HEALTH_STATE_TONE: Record<IntegrationHealthState, StatusDotTone> = {
  unknown: 'neutral',
  healthy: 'success',
  auth_failed: 'danger',
  unreachable: 'warn',
};

export const DINGTALK_STREAM_STATE_TONE: Record<DingTalkStreamState, StatusDotTone> = {
  connected: 'success',
  reconnecting: 'warn',
  down: 'danger',
  disabled: 'neutral',
};

export const QUEUE_STATE_TONE: Record<QueueItemState, StatusDotTone> = {
  pending: 'neutral',
  dispatching: 'info',
  processing: 'info',
  cancelling: 'warn',
  done: 'success',
  failed: 'danger',
  cancelled: 'neutral',
};

/** 健康度收窄(`:test` 响应为 string 契约;未知值归 `unknown`,边界处防御,§6.15)。 */
export function toHealthState(value: string): IntegrationHealthState {
  return (INTEGRATION_HEALTH_STATES as ReadonlyArray<string>).includes(value)
    ? (value as IntegrationHealthState)
    : 'unknown';
}

/** 未知持久状态 fail-closed 渲染为 down，不伪装为健康。 */
export function toDingTalkStreamState(value: string): DingTalkStreamState {
  return (DINGTALK_STREAM_STATES as ReadonlyArray<string>).includes(value)
    ? (value as DingTalkStreamState)
    : 'down';
}

/** 只展示会话键的外部 ref 段；畸形键原样回退，便于排障。 */
export function conversationDisplayName(conversationKey: string): string {
  const first = conversationKey.indexOf(':');
  const second = first < 0 ? -1 : conversationKey.indexOf(':', first + 1);
  return second < 0 || second === conversationKey.length - 1
    ? conversationKey
    : conversationKey.slice(second + 1);
}

/** 防御性摘要净化；API 同样保证 ≤120 字且不含控制符，前端边界再收窄一次。 */
export function sanitizeMessageExcerpt(excerpt: string): string {
  const stripped = Array.from(excerpt)
    .filter((character) => {
      const codePoint = character.codePointAt(0) ?? 0;
      return !(
        codePoint <= 0x1f ||
        (codePoint >= 0x7f && codePoint <= 0x9f) ||
        codePoint === 0x00ad ||
        codePoint === 0x180e ||
        (codePoint >= 0x200b && codePoint <= 0x200f) ||
        (codePoint >= 0x2028 && codePoint <= 0x202f) ||
        (codePoint >= 0x2060 && codePoint <= 0x2064) ||
        (codePoint >= 0x2066 && codePoint <= 0x206f) ||
        codePoint === 0xfeff
      );
    })
    .join('');
  return Array.from(stripped.replace(/\s+/gu, ' ').trim()).slice(0, 120).join('');
}

/** 当前用户 external identities 与队列 sender.identity_key 的完整三元组比对键。 */
export function externalIdentityTriple(
  identity: Pick<ExternalIdentity, 'provider' | 'provider_tenant_key' | 'external_user_key'>,
): string {
  return `${identity.provider}:${identity.provider_tenant_key}:${identity.external_user_key}`;
}

/** Queue runtime in a compact, locale-neutral timer shape (m:ss or h:mm:ss). */
export function formatQueueDuration(
  startedAt: string,
  finishedAt: string | null,
  nowMs: number,
): string | null {
  const startMs = Date.parse(startedAt);
  const endMs = finishedAt === null ? nowMs : Date.parse(finishedAt);
  if (Number.isNaN(startMs) || !Number.isFinite(endMs)) return null;
  const totalSeconds = Math.max(0, Math.floor((endMs - startMs) / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const paddedSeconds = String(seconds).padStart(2, '0');
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, '0')}:${paddedSeconds}`
    : `${minutes}:${paddedSeconds}`;
}

export const SIGNATURE_STATUS_TONE: Record<SignatureStatus, StatusDotTone> = {
  valid: 'success',
  invalid: 'danger',
  missing: 'warn',
};

export const PROCESS_STATUS_TONE: Record<ProcessStatus, StatusDotTone> = {
  received: 'info',
  matched: 'info',
  dispatched: 'success',
  deduped: 'warn',
  rejected: 'danger',
  processed: 'success',
  failed: 'danger',
};

export const SUBSCRIPTION_STATUS_TONE: Record<SubscriptionStatus, StatusDotTone> = {
  active: 'success',
  paused: 'warn',
  disabled: 'danger',
};

export const DELIVERY_STATE_TONE: Record<DeliveryState, StatusDotTone> = {
  pending: 'info',
  sent: 'success',
  failed: 'danger',
};

export const BINDING_STATUS_TONE: Record<BindingStatus, StatusDotTone> = {
  active: 'success',
  disabled: 'neutral',
};

export const VCS_LINK_STATUS_TONE: Record<VcsLinkStatus, StatusDotTone> = {
  active: 'success',
  stale: 'warn',
  deleted: 'neutral',
};

/** 相对时间(台账/投递时间线;展示层本地化由 Intl 完成)。 */
export function formatRelativeTime(
  iso: string | null,
  nowMs: number,
  locale: string,
): string | null {
  if (iso === null) return null;
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return null;
  const diffSeconds = Math.round((nowMs - then) / 1000);
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' });
  if (Math.abs(diffSeconds) < 60) return rtf.format(-diffSeconds, 'second');
  const diffMinutes = Math.round(diffSeconds / 60);
  if (Math.abs(diffMinutes) < 60) return rtf.format(-diffMinutes, 'minute');
  const diffHours = Math.round(diffMinutes / 60);
  if (Math.abs(diffHours) < 24) return rtf.format(-diffHours, 'hour');
  const diffDays = Math.round(diffHours / 24);
  return rtf.format(-diffDays, 'day');
}

/** 出向目标 URL 客户端 https 预检(§5.3 https-only;权威校验在后端)。 */
export function isHttpsUrl(url: string): boolean {
  const trimmed = url.trim();
  if (trimmed === '') return false;
  try {
    return new URL(trimmed).protocol === 'https:';
  } catch {
    return false;
  }
}

/**
 * 外部深链 scheme 防御(§6.15 不可信内容):仅放行 http/https,
 * 拒绝 `javascript:`/`data:` 等可执行 scheme;非法 URL 同样拒绝。
 */
export function isSafeWebUrl(url: string): boolean {
  try {
    const protocol = new URL(url).protocol;
    return protocol === 'https:' || protocol === 'http:';
  } catch {
    return false;
  }
}

/** 出向订阅成功率(§4.1:null / 非有限值 → `—`;0..1 → 整数百分比)。 */
export function formatSuccessRate(rate: number | null): string {
  if (rate === null || !Number.isFinite(rate)) return '—';
  return `${Math.round(rate * 100)}%`;
}

/** VCS 关联 external_state 快照渲染(如 `{pr_state:'merged'}` → `pr_state=merged`)。 */
export function formatExternalState(
  state: Readonly<Record<string, unknown>> | null,
): string | null {
  if (state === null) return null;
  const parts = Object.entries(state)
    .filter(([, value]) => typeof value === 'string' || typeof value === 'number')
    .map(([key, value]) => `${key}=${String(value)}`);
  return parts.length > 0 ? parts.join(' · ') : null;
}

/** 熔断判定(§2.5:disabled 且 fail_count>0 视为熔断,区别于人工 paused)。 */
export function isTripped(subscription: {
  readonly status: SubscriptionStatus;
  readonly fail_count: number;
}): boolean {
  return subscription.status === 'disabled' && subscription.fail_count > 0;
}
