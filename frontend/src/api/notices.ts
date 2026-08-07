/**
 * API 契约通知总线(L252)— client 拦截层 → UI 桥(ApiNoticeToasts)的解耦通道。
 *
 * - 429 退避:每次 429 发 rate_limited 通知(携带解析后的 Retry-After 秒数);
 *   最小间隔内的重复通知被去抖抑制,避免并发请求风暴刷爆 toast 区。
 * - Deprecation/Sunset(README §6 版本策略):携带弃用头的响应每会话只提示一次升级。
 *
 * 本模块不依赖 React、不硬编码可见文案;呈现与 i18n 由订阅方负责。
 * emit 永不向请求路径冒泡:单个监听器抛错被隔离,其余监听器照常收到通知。
 */

export type ApiNoticeKind = 'rate_limited' | 'deprecated';

export interface ApiNotice {
  kind: ApiNoticeKind;
  /** rate_limited:Retry-After 秒数;响应未携带可解析的头时为 undefined */
  retryAfterSeconds?: number;
  /** deprecated:Sunset 响应头原值(仅透传,不经本模块渲染);未携带为 undefined */
  sunset?: string;
}

export type ApiNoticeListener = (notice: ApiNotice) => void;

/** 429 通知最小间隔(ms):窗口内的重复通知被抑制,避免并发风暴刷屏 */
export const RATE_LIMIT_NOTICE_MIN_INTERVAL_MS = 2000;

let listeners: ReadonlyArray<ApiNoticeListener> = [];
let deprecationNotified = false;
let lastRateLimitedAt: number | null = null;

/** 订阅契约通知;返回退订函数。 */
export function onApiNotice(listener: ApiNoticeListener): () => void {
  listeners = [...listeners, listener];
  return () => {
    listeners = listeners.filter((item) => item !== listener);
  };
}

/** 向全部监听器广播一条通知;单个监听器抛错不影响其余监听器。 */
function emitApiNotice(notice: ApiNotice): void {
  for (const listener of listeners) {
    try {
      listener(notice);
    } catch {
      // 呈现层故障不得拖垮请求路径;静默隔离即可(本总线无更有意义的上报通道)。
    }
  }
}

/**
 * 429 退避通知:距上次通知不足 RATE_LIMIT_NOTICE_MIN_INTERVAL_MS 时抑制。
 * retryAfterSeconds 为 undefined 表示响应未携带可解析的 Retry-After。
 */
export function notifyRateLimited(retryAfterSeconds: number | undefined): void {
  const now = Date.now();
  if (lastRateLimitedAt !== null && now - lastRateLimitedAt < RATE_LIMIT_NOTICE_MIN_INTERVAL_MS) {
    return;
  }
  lastRateLimitedAt = now;
  emitApiNotice({ kind: 'rate_limited', retryAfterSeconds });
}

/**
 * Deprecation/Sunset 提示:任一头存在即触发;每会话仅一次(一次性去抖)。
 * 两个头皆缺 → 无操作。
 */
export function notifyDeprecation(deprecation: string | null, sunset: string | null): void {
  if (deprecation === null && sunset === null) return;
  if (deprecationNotified) return;
  deprecationNotified = true;
  emitApiNotice({ kind: 'deprecated', sunset: sunset ?? undefined });
}

/** 重置全部总线状态(监听器 + 去抖标志;仅测试用)。 */
export function resetApiNoticeState(): void {
  listeners = [];
  deprecationNotified = false;
  lastRateLimitedAt = null;
}
