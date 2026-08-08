/**
 * API 契约通知总线测试(L252)— 429 退避提示与 Deprecation/Sunset 一次性去抖。
 * client 拦截层只发通知,呈现由 UI 桥(ApiNoticeToasts)负责,故此处纯逻辑验证。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ApiNotice } from '../notices';
import {
  RATE_LIMIT_NOTICE_MIN_INTERVAL_MS,
  notifyDeprecation,
  notifyRateLimited,
  onApiNotice,
  resetApiNoticeState,
} from '../notices';

function collect(): { notices: ApiNotice[]; unsubscribe: () => void } {
  const notices: ApiNotice[] = [];
  const unsubscribe = onApiNotice((notice) => {
    notices.push(notice);
  });
  return { notices, unsubscribe };
}

beforeEach(() => {
  resetApiNoticeState();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('Deprecation/Sunset 一次性提示(client 拦截层去抖)', () => {
  it('响应携带 Deprecation 头 → 发出一次 deprecated 通知', () => {
    const { notices, unsubscribe } = collect();
    notifyDeprecation('true', null);
    expect(notices).toEqual([{ kind: 'deprecated', sunset: undefined }]);
    unsubscribe();
  });

  it('仅携带 Sunset 头 → 同样发出通知并透传 sunset 值', () => {
    const { notices, unsubscribe } = collect();
    notifyDeprecation(null, 'Sun, 01 Nov 2026 00:00:00 GMT');
    expect(notices).toEqual([{ kind: 'deprecated', sunset: 'Sun, 01 Nov 2026 00:00:00 GMT' }]);
    unsubscribe();
  });

  it('同一会话内只提示一次:第二次携带弃用头的响应不再发通知', () => {
    const { notices, unsubscribe } = collect();
    notifyDeprecation('true', null);
    notifyDeprecation('true', 'Sun, 01 Nov 2026 00:00:00 GMT');
    expect(notices).toHaveLength(1);
    unsubscribe();
  });

  it('两头皆缺 → 不发通知', () => {
    const { notices, unsubscribe } = collect();
    notifyDeprecation(null, null);
    expect(notices).toEqual([]);
    unsubscribe();
  });

  it('resetApiNoticeState 重新武装一次性弃用提示(测试隔离用)', () => {
    const first = collect();
    notifyDeprecation('true', null);
    first.unsubscribe();
    resetApiNoticeState();
    const second = collect();
    notifyDeprecation('true', null);
    expect(first.notices).toHaveLength(1);
    expect(second.notices).toHaveLength(1);
    second.unsubscribe();
  });
});

describe('429 退避通知(Retry-After 秒数)', () => {
  it('携带 Retry-After 秒数发出 rate_limited 通知', () => {
    const { notices, unsubscribe } = collect();
    notifyRateLimited(30);
    expect(notices).toEqual([{ kind: 'rate_limited', retryAfterSeconds: 30 }]);
    unsubscribe();
  });

  it('无 Retry-After 时秒数为 undefined(提示仍要可见)', () => {
    const { notices, unsubscribe } = collect();
    notifyRateLimited(undefined);
    expect(notices).toEqual([{ kind: 'rate_limited', retryAfterSeconds: undefined }]);
    unsubscribe();
  });

  it('最小间隔内的重复 429 被去抖抑制,间隔过后恢复提示', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
    const { notices, unsubscribe } = collect();

    notifyRateLimited(30);
    notifyRateLimited(30);
    expect(notices).toHaveLength(1);

    vi.setSystemTime(
      new Date('2026-01-01T00:00:00Z').getTime() + RATE_LIMIT_NOTICE_MIN_INTERVAL_MS,
    );
    notifyRateLimited(10);
    expect(notices).toHaveLength(2);
    expect(notices[1]).toEqual({ kind: 'rate_limited', retryAfterSeconds: 10 });
    unsubscribe();
  });
});

describe('订阅管理', () => {
  it('unsubscribe 后不再收到通知', () => {
    const { notices, unsubscribe } = collect();
    unsubscribe();
    notifyRateLimited(5);
    expect(notices).toEqual([]);
  });

  it('单个监听器抛错不影响其余监听器,也不向请求路径冒泡', () => {
    const unsubscribeBad = onApiNotice(() => {
      throw new Error('bad listener');
    });
    const { notices, unsubscribe } = collect();
    expect(() => notifyRateLimited(5)).not.toThrow();
    expect(notices).toHaveLength(1);
    unsubscribeBad();
    unsubscribe();
  });

  it('resetApiNoticeState 清空监听器(测试间完全隔离)', () => {
    const { notices } = collect();
    resetApiNoticeState();
    notifyRateLimited(5);
    expect(notices).toEqual([]);
  });
});
