/**
 * 会话列表实时合并纯函数测试(chat-session.md §3.6 / README §6.7)。
 * 重点:不可变(无变化返回原引用)、防回退、未知会话忽略、仅预览事件生效。
 */
import { describe, expect, it } from 'vitest';
import type { RealtimeEventFrame } from '../../../types/realtime';
import { applySessionListFrame } from '../realtime';
import type { ChatSession } from '../types';

function makeSession(overrides: Partial<ChatSession> = {}): ChatSession {
  return {
    id: 'sess-1',
    workspace_id: 'ws-1',
    owner_id: 'user-1',
    agent_id: 'agent-1',
    agent: { id: 'agent-1', name: 'Bot', avatar_url: null },
    title: 'Chat',
    title_is_auto: true,
    context_issue_id: null,
    context_project_id: null,
    status: 'active',
    pinned: false,
    last_message_at: '2026-07-01T00:00:00Z',
    last_message_preview: 'hello',
    message_count: 2,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
    ...overrides,
  };
}

function frame(event: string, payload: Record<string, unknown>): RealtimeEventFrame {
  return { op: 'event', channel: 'workspace:ws-1:chat_sessions', seq: 1, event, payload };
}

describe('applySessionListFrame(§3.6 列表级预览合并)', () => {
  it('非预览事件原样返回(delta 不入列表)', () => {
    const sessions = [makeSession()];
    const next = applySessionListFrame(sessions, frame('message.delta', { session_id: 'sess-1' }));
    expect(next).toBe(sessions);
  });

  it('缺少 session_id 原样返回', () => {
    const sessions = [makeSession()];
    const next = applySessionListFrame(sessions, frame('message.done', { message_id: 'm-1' }));
    expect(next).toBe(sessions);
  });

  it('未知会话忽略', () => {
    const sessions = [makeSession()];
    const next = applySessionListFrame(sessions, frame('message.done', { session_id: 'other' }));
    expect(next).toBe(sessions);
  });

  it('message.done 并入预览字段并返回新数组(其余会话引用不变)', () => {
    const other = makeSession({ id: 'sess-2' });
    const sessions = [makeSession(), other];
    const next = applySessionListFrame(
      sessions,
      frame('message.done', {
        session_id: 'sess-1',
        last_message_preview: 'world',
        last_message_at: '2026-07-02T00:00:00Z',
        message_count: 3,
      }),
    );
    expect(next).not.toBe(sessions);
    expect(next[0].last_message_preview).toBe('world');
    expect(next[0].last_message_at).toBe('2026-07-02T00:00:00Z');
    expect(next[0].message_count).toBe(3);
    expect(next[1]).toBe(other);
  });

  it('message.created 同样并入', () => {
    const sessions = [makeSession()];
    const next = applySessionListFrame(
      sessions,
      frame('message.created', { session_id: 'sess-1', last_message_preview: 'new' }),
    );
    expect(next[0].last_message_preview).toBe('new');
  });

  it('字段无变化时返回原引用', () => {
    const sessions = [makeSession()];
    const next = applySessionListFrame(
      sessions,
      frame('message.done', {
        session_id: 'sess-1',
        last_message_preview: 'hello',
        last_message_at: '2026-07-01T00:00:00Z',
        message_count: 2,
      }),
    );
    expect(next).toBe(sessions);
  });

  it('防回退:帧 last_message_at 更旧则丢弃', () => {
    const sessions = [makeSession({ last_message_at: '2026-07-05T00:00:00Z' })];
    const next = applySessionListFrame(
      sessions,
      frame('message.done', {
        session_id: 'sess-1',
        last_message_preview: 'stale',
        last_message_at: '2026-07-01T00:00:00Z',
      }),
    );
    expect(next).toBe(sessions);
  });

  it('现有 last_message_at 为 null 时不判为过期', () => {
    const sessions = [makeSession({ last_message_at: null })];
    const next = applySessionListFrame(
      sessions,
      frame('message.done', {
        session_id: 'sess-1',
        last_message_at: '2026-07-02T00:00:00Z',
        last_message_preview: 'first',
      }),
    );
    expect(next[0].last_message_at).toBe('2026-07-02T00:00:00Z');
  });

  it('仅 message_count 变化也产出新对象', () => {
    const sessions = [makeSession()];
    const next = applySessionListFrame(
      sessions,
      frame('message.done', { session_id: 'sess-1', message_count: 9 }),
    );
    expect(next).not.toBe(sessions);
    expect(next[0].message_count).toBe(9);
    expect(next[0].last_message_preview).toBe('hello');
  });

  it('非法字段类型被忽略(不污染)', () => {
    const sessions = [makeSession()];
    const next = applySessionListFrame(
      sessions,
      frame('message.done', { session_id: 'sess-1', message_count: 'not-a-number' }),
    );
    expect(next).toBe(sessions);
  });
});
