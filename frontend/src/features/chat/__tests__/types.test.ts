/**
 * 聊天类型层运行时常量测试(chat-session.md §3.3 终态事件集合)。
 */
import { describe, expect, it } from 'vitest';
import { TERMINAL_STREAM_EVENTS } from '../types';

describe('TERMINAL_STREAM_EVENTS(§6.8:命中即停止重连)', () => {
  it('包含且仅包含三个终态事件', () => {
    expect(TERMINAL_STREAM_EVENTS.has('message.done')).toBe(true);
    expect(TERMINAL_STREAM_EVENTS.has('message.interrupted')).toBe(true);
    expect(TERMINAL_STREAM_EVENTS.has('error')).toBe(true);
    expect(TERMINAL_STREAM_EVENTS.size).toBe(3);
  });

  it('非终态事件不在集合内(delta / created / ping 须续流)', () => {
    expect(TERMINAL_STREAM_EVENTS.has('message.delta')).toBe(false);
    expect(TERMINAL_STREAM_EVENTS.has('message.created')).toBe(false);
    expect(TERMINAL_STREAM_EVENTS.has('ping')).toBe(false);
  });
});
