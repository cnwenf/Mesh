/**
 * MessageList 测试(chat-session.md §4.2):空态、消息渲染、加载更旧回调、
 * 引用解析(quote_message_id → 被引消息预览)、agent 多候选附 CandidateSwitcher。
 */
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api';
import { fakeResponse, stubFetch } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { MessageList } from '../MessageList';
import type { ChatMessage } from '../types';

function makeMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: 'm-1', session_id: 'sess-1', role: 'agent', content: 'Hi', generation_id: null,
    generation_status: 'done', parent_id: null, selected_candidate: true, quote_message_id: null,
    prompt_tokens: null, completion_tokens: null, error_message: null, started_at: null,
    finished_at: null, created_at: '2026-07-01T00:00:00Z', attachments: [],
    candidate_count: null, candidate_index: null, ...overrides,
  };
}

const client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null, fetchImpl: stubFetch(fakeResponse({ body: { data: [], next_cursor: null } })).fetchImpl });

function renderList(props: Partial<React.ComponentProps<typeof MessageList>> = {}) {
  return renderWithProviders(
    <MessageList
      messages={[]}
      displayMessages={[]}
      locale="en"
      nextCursor={null}
      isLoadingMore={false}
      client={client}
      workspaceId="ws-1"
      sessionId="sess-1"
      listRef={{ current: null }}
      onLoadOlder={vi.fn()}
      onRegenerate={vi.fn()}
      onSelectCandidate={vi.fn()}
      onQuote={vi.fn()}
      {...props}
    />,
  );
}

describe('MessageList(§4.2)', () => {
  it('空列表呈现空态', () => {
    renderList();
    expect(screen.getByText('No messages yet')).toBeInTheDocument();
  });

  it('渲染消息气泡', () => {
    const msg = makeMessage({ id: 'm-1', content: 'hello world' });
    renderList({ messages: [msg], displayMessages: [msg] });
    expect(screen.getByTestId('chat-message-m-1')).toBeInTheDocument();
  });

  it('有 nextCursor 时呈现加载更旧并触发回调', async () => {
    const user = userEvent.setup();
    const onLoadOlder = vi.fn();
    const msg = makeMessage();
    renderList({ messages: [msg], displayMessages: [msg], nextCursor: 'cur', onLoadOlder });
    await user.click(screen.getByTestId('chat-load-older'));
    expect(onLoadOlder).toHaveBeenCalledTimes(1);
  });

  it('解析引用消息并呈现预览', () => {
    const quoted = makeMessage({ id: 'q-1', role: 'user', content: 'original question' });
    const reply = makeMessage({ id: 'm-2', content: 'reply', quote_message_id: 'q-1' });
    renderList({ messages: [quoted, reply], displayMessages: [quoted, reply] });
    expect(screen.getByTestId('chat-quote-m-2')).toHaveTextContent('original question');
  });

  it('引用目标缺失时不渲染预览', () => {
    const reply = makeMessage({ id: 'm-2', content: 'reply', quote_message_id: 'missing' });
    renderList({ messages: [reply], displayMessages: [reply] });
    expect(screen.queryByTestId('chat-quote-m-2')).toBeNull();
  });

  it('agent 多候选消息附带候选切换器', () => {
    const msg = makeMessage({ id: 'm-a', role: 'agent', parent_id: 'u-1', candidate_count: 2, candidate_index: 0 });
    renderList({ messages: [msg], displayMessages: [msg] });
    expect(screen.getByTestId('chat-candidates-m-a')).toBeInTheDocument();
  });

  it('user 消息不附候选切换器', () => {
    const msg = makeMessage({ id: 'm-u', role: 'user', parent_id: null, candidate_count: 2 });
    renderList({ messages: [msg], displayMessages: [msg] });
    expect(screen.queryByTestId('chat-candidates-m-u')).toBeNull();
  });
});
