/**
 * realtime 补充覆盖:entityOf 无点事件(branch L21)、read 帧 id 非字符串
 * (branch L47/L48)、notification 未知子动作落到末尾 return(branch L57 + stmts 59/60)。
 */
import { describe, expect, it } from 'vitest';
import type { RealtimeEventFrame } from '../../../types/realtime';
import { applyInboxFrame } from '../realtime';
import type { Notification } from '../types';

function frame(event: string, payload: unknown): RealtimeEventFrame {
  return { op: 'event', channel: 'member:mem-1:inbox', seq: 1, event, payload } as RealtimeEventFrame;
}

const N1: Notification = {
  id: 'n-1',
  type: 'mentioned',
  priority: 'normal',
  issue_id: 'iss-1',
  comment_id: null,
  execution_id: null,
  group_key: null,
  actor: null,
  preview: '',
  title: 'old',
  count: 1,
  read_at: null,
  archived_at: null,
  created_at: '2026-07-01T00:00:00Z',
  latest_comment_id: null,
};

describe('applyInboxFrame (补充覆盖)', () => {
  it('treats a dot-less event as a non-notification entity (entityOf branch L21)', () => {
    const list = [N1];
    // 事件名无 '.' → entityOf 返回 '' → 非 notification → 原样返回
    expect(applyInboxFrame(list, frame('ping', {}))).toBe(list);
  });

  it('ignores a read frame whose id is not a string (branches L47 + L48)', () => {
    const list = [N1];
    // id 非字符串 → 解析为 undefined → 提前返回原引用
    expect(applyInboxFrame(list, frame('notification.read', { id: 123 }))).toBe(list);
  });

  it('falls through for an unknown notification sub-action (branch L57 + final return)', () => {
    const list = [N1];
    // entity=notification 但 action 既非 created 也非 read → 落到末尾 return
    expect(applyInboxFrame(list, frame('notification.deleted', {}))).toBe(list);
  });
});
