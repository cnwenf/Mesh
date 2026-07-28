/**
 * MessageBubble 测试(chat-session.md §4.2):角色朝向 / AI 徽标 / 流式光标 /
 * 失败·中断态 + 重试 / 附件卡片 / 引用预览 / Markdown 净化渲染 / 字节数本地化。
 */
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../../test-utils/render';
import { MessageBubble, formatByteSize } from '../MessageBubble';
import type { ChatMessage } from '../types';

function makeMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: 'm-1',
    session_id: 'sess-1',
    role: 'agent',
    content: 'Hello',
    generation_id: null,
    generation_status: 'done',
    parent_id: null,
    selected_candidate: true,
    quote_message_id: null,
    prompt_tokens: null,
    completion_tokens: null,
    error_message: null,
    started_at: null,
    finished_at: null,
    created_at: '2026-07-01T00:00:00Z',
    attachments: [],
    candidate_count: null,
    candidate_index: null,
    ...overrides,
  };
}

describe('formatByteSize', () => {
  it('B / KB / MB 粗粒度换算', () => {
    expect(formatByteSize(512)).toBe('512 B');
    expect(formatByteSize(2048)).toBe('2.0 KB');
    expect(formatByteSize(5 * 1024 * 1024)).toBe('5.0 MB');
  });
});

describe('MessageBubble(§4.2)', () => {
  it('agent 消息渲染 AI 徽标与正文', () => {
    renderWithProviders(<MessageBubble message={makeMessage()} locale="en" />);
    expect(screen.getByTestId('chat-ai-badge')).toBeInTheDocument();
    expect(screen.getByTestId('chat-body-m-1').innerHTML).toContain('Hello');
  });

  it('user 消息不渲染 AI 徽标', () => {
    renderWithProviders(<MessageBubble message={makeMessage({ role: 'user' })} locale="en" />);
    expect(screen.queryByTestId('chat-ai-badge')).toBeNull();
  });

  it('Markdown 经净化渲染(粗体 → strong)', () => {
    renderWithProviders(
      <MessageBubble message={makeMessage({ content: '**bold**' })} locale="en" />,
    );
    expect(screen.getByTestId('chat-body-m-1').querySelector('strong')).not.toBeNull();
  });

  it('流式态渲染光标', () => {
    renderWithProviders(
      <MessageBubble message={makeMessage({ generation_status: 'streaming' })} locale="en" />,
    );
    expect(screen.getByTestId('chat-cursor-m-1')).toBeInTheDocument();
  });

  it('失败态呈现错误文案与重试入口', async () => {
    const user = userEvent.setup();
    const onRegenerate = vi.fn();
    renderWithProviders(
      <MessageBubble
        message={makeMessage({ generation_status: 'failed', error_message: 'boom' })}
        locale="en"
        onRegenerate={onRegenerate}
      />,
    );
    expect(screen.getByTestId('chat-error-m-1')).toHaveTextContent('boom');
    await user.click(screen.getByTestId('chat-regenerate-m-1'));
    expect(onRegenerate).toHaveBeenCalledTimes(1);
  });

  it('失败态无 error_message 时回退本地文案', () => {
    renderWithProviders(
      <MessageBubble message={makeMessage({ generation_status: 'failed' })} locale="en" />,
    );
    expect(screen.getByTestId('chat-error-m-1').textContent).not.toBe('');
  });

  it('中断态呈现提示', () => {
    renderWithProviders(
      <MessageBubble message={makeMessage({ generation_status: 'interrupted' })} locale="en" />,
    );
    expect(screen.getByTestId('chat-interrupted-m-1')).toBeInTheDocument();
  });

  it('附件卡片渲染文件名与大小', () => {
    renderWithProviders(
      <MessageBubble
        message={makeMessage({
          attachments: [
            {
              id: 'a-1',
              file_name: 'r.pdf',
              mime_type: 'application/pdf',
              byte_size: 2048,
              scan_status: 'clean',
            },
          ],
        })}
        locale="en"
      />,
    );
    const list = screen.getByTestId('chat-attachments-m-1');
    expect(list).toHaveTextContent('r.pdf');
    expect(list).toHaveTextContent('2.0 KB');
  });

  it('引用预览渲染被引消息截断正文', () => {
    const quoted = makeMessage({ id: 'q-1', content: 'original text' });
    renderWithProviders(
      <MessageBubble
        message={makeMessage({ quote_message_id: 'q-1' })}
        locale="en"
        quotedMessage={quoted}
      />,
    );
    expect(screen.getByTestId('chat-quote-m-1')).toHaveTextContent('original text');
  });

  it('引用操作回调', async () => {
    const user = userEvent.setup();
    const onQuote = vi.fn();
    renderWithProviders(<MessageBubble message={makeMessage()} locale="en" onQuote={onQuote} />);
    await user.click(screen.getByTestId('chat-quote-action-m-1'));
    expect(onQuote).toHaveBeenCalledTimes(1);
  });

  it('流式态不渲染重生成按钮', () => {
    renderWithProviders(
      <MessageBubble
        message={makeMessage({ generation_status: 'streaming' })}
        locale="en"
        onRegenerate={vi.fn()}
      />,
    );
    expect(screen.queryByTestId('chat-regenerate-m-1')).toBeNull();
  });

  it('user 消息即便提供 onRegenerate 也不渲染重生成', () => {
    renderWithProviders(
      <MessageBubble message={makeMessage({ role: 'user' })} locale="en" onRegenerate={vi.fn()} />,
    );
    expect(screen.queryByTestId('chat-regenerate-m-1')).toBeNull();
  });

  it('system 消息隐藏原始 fence,仅呈现上下文提示 chip(§6.15)', () => {
    const fence = '<<<UNTRUSTED ISSUE CONTEXT>>>\ntitle: secret issue\n<<<END>>>';
    const { container } = renderWithProviders(
      <MessageBubble message={makeMessage({ role: 'system', content: fence })} locale="en" />,
    );
    // 呈现弱化提示 chip
    expect(screen.getByTestId('chat-context-linked')).toBeInTheDocument();
    expect(screen.getByTestId('chat-message-m-1')).toHaveAttribute('data-role', 'system');
    // 原始 fence 内容绝不外露
    expect(container.textContent).not.toContain('UNTRUSTED ISSUE CONTEXT');
    expect(container.textContent).not.toContain('secret issue');
    // 不渲染正文气泡体
    expect(screen.queryByTestId('chat-body-m-1')).toBeNull();
  });
});
