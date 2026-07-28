/**
 * 桶导出完整性测试:确保 index.ts 公共 API 表面稳定(页面/组件/hook/契约/类型)。
 */
import { describe, expect, it } from 'vitest';
import * as Chat from '../index';

describe('chat 模块桶导出', () => {
  it('导出页面与组件', () => {
    expect(typeof Chat.ChatPage).toBe('function');
    expect(typeof Chat.ConversationPanel).toBe('function');
    expect(typeof Chat.SessionListPanel).toBe('function');
    expect(typeof Chat.NewSessionDialog).toBe('function');
    expect(typeof Chat.DistillDialog).toBe('function');
    expect(typeof Chat.MessageBubble).toBe('function');
    expect(typeof Chat.CandidateSwitcher).toBe('function');
    expect(typeof Chat.ChatComposer).toBe('function');
  });

  it('导出 hook / 流式 / 实时合并', () => {
    expect(typeof Chat.useChatStream).toBe('function');
    expect(typeof Chat.streamChatGeneration).toBe('function');
    expect(typeof Chat.parseSseBlock).toBe('function');
    expect(typeof Chat.parseStreamEvent).toBe('function');
    expect(typeof Chat.applySessionListFrame).toBe('function');
  });

  it('导出契约层函数与频道助手', () => {
    expect(typeof Chat.createChatSession).toBe('function');
    expect(typeof Chat.listChatSessions).toBe('function');
    expect(typeof Chat.sendMessage).toBe('function');
    expect(typeof Chat.stopGeneration).toBe('function');
    expect(typeof Chat.distillPreview).toBe('function');
    expect(typeof Chat.chatSessionChannel).toBe('function');
    expect(typeof Chat.chatListChannel).toBe('function');
    expect(typeof Chat.formatByteSize).toBe('function');
    expect(Chat.TERMINAL_STREAM_EVENTS.size).toBe(3);
  });
});
