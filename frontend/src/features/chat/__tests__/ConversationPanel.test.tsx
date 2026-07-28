/**
 * ConversationPanel 核心测试(chat-session.md §4.2):消息加载(倒序反转)、空/错/载态、
 * 发送全链路(乐观用户消息 → sendMessage → SSE 流式 agent 回复 → 终态并入 + 重拉)。
 * client 经 prop 注入(URL 路由桩);SSE 经全局 fetch 桩(每次新建 ReadableStream 体)。
 */
import { screen, waitFor } from '@testing-library/react';
import { fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { ConversationPanel } from '../ConversationPanel';
import type { ChatMessage, ChatSession } from '../types';

const encoder = new TextEncoder();

function makeSession(overrides: Partial<ChatSession> = {}): ChatSession {
  return {
    id: 'sess-1',
    workspace_id: 'ws-1',
    owner_id: 'u-1',
    agent_id: 'a-1',
    agent: { id: 'a-1', name: 'Bot', avatar_url: null },
    title: 'Chat',
    title_is_auto: true,
    context_issue_id: null,
    context_project_id: null,
    status: 'active',
    pinned: false,
    last_message_at: null,
    last_message_preview: null,
    message_count: 0,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
    ...overrides,
  };
}

function makeMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: 'm-1',
    session_id: 'sess-1',
    role: 'agent',
    content: 'Hi',
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

type Router = (url: string, method: string) => Response | null;

function routedClient(router: Router): MeshApiClient {
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    const response = router(url, method);
    if (response !== null) return response;
    return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'x' } } });
  }) as typeof fetch;
  return new MeshApiClient({ baseUrl: 'http://api', getToken: () => null, fetchImpl });
}

/** 每次调用新建 SSE 响应(ReadableStream 体仅可消费一次)。 */
function sseFetch(frames: readonly string[]): typeof fetch {
  return (async () => ({
    ok: true,
    status: 200,
    body: new ReadableStream<Uint8Array>({
      start(controller) {
        for (const frame of frames) controller.enqueue(encoder.encode(frame));
        controller.close();
      },
    }),
    headers: { get: () => null },
  })) as unknown as typeof fetch;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('ConversationPanel(§4.2)', () => {
  it('加载消息(时间倒序 → 反转升序展示)', async () => {
    const agentMsg = makeMessage({ id: 'm-a', role: 'agent', content: 'answer' });
    const userMsg = makeMessage({ id: 'm-u', role: 'user', content: 'question' });
    const client = routedClient((url) =>
      url.includes('/messages') ? fakeResponse({ body: { data: [agentMsg, userMsg], next_cursor: null } }) : null,
    );
    renderWithProviders(
      <ConversationPanel client={client} workspaceId="ws-1" session={makeSession()} locale="en" onSessionUpdated={vi.fn()} />,
    );
    expect(await screen.findByTestId('chat-message-m-u')).toBeInTheDocument();
    expect(screen.getByTestId('chat-message-m-a')).toBeInTheDocument();
    // 升序:用户消息在前
    const list = screen.getByTestId('chat-messages');
    expect(list.textContent?.indexOf('question')).toBeLessThan(list.textContent?.indexOf('answer') ?? -1);
  });

  it('空会话呈现空态', async () => {
    const client = routedClient((url) =>
      url.includes('/messages') ? fakeResponse({ body: { data: [], next_cursor: null } }) : null,
    );
    renderWithProviders(
      <ConversationPanel client={client} workspaceId="ws-1" session={makeSession()} locale="en" onSessionUpdated={vi.fn()} />,
    );
    expect(await screen.findByText('No messages yet')).toBeInTheDocument();
  });

  it('加载失败呈现错误态并可重试', async () => {
    let calls = 0;
    const client = routedClient((url) => {
      if (!url.includes('/messages')) return null;
      calls += 1;
      return calls === 1
        ? fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } })
        : fakeResponse({ body: { data: [], next_cursor: null } });
    });
    renderWithProviders(
      <ConversationPanel client={client} workspaceId="ws-1" session={makeSession()} locale="en" onSessionUpdated={vi.fn()} />,
    );
    expect(await screen.findByText('Something went wrong')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /retry/i }));
    expect(await screen.findByText('No messages yet')).toBeInTheDocument();
  });

  it('加载态渲染骨架', () => {
    // 永不 settle 的 client(挂起)→ 持续 loading。
    const fetchImpl = (() => new Promise<Response>(() => undefined)) as unknown as typeof fetch;
    const client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null, fetchImpl });
    renderWithProviders(
      <ConversationPanel client={client} workspaceId="ws-1" session={makeSession()} locale="en" onSessionUpdated={vi.fn()} />,
    );
    expect(screen.getByText('Loading…')).toBeInTheDocument();
  });

  it('发送全链路:乐观用户消息 → SSE agent 回复 → 终态并入', async () => {
    const seedUser = makeMessage({ id: 'u-seed', role: 'user', content: 'seed' });
    let messageCalls = 0;
    const client = routedClient((url, method) => {
      if (url.includes('/messages') && method === 'GET') {
        messageCalls += 1;
        if (messageCalls === 1) {
          return fakeResponse({ body: { data: [seedUser], next_cursor: null } });
        }
        // 重拉:返回含新消息的完整列表(时间倒序)
        return fakeResponse({
          body: {
            data: [
              makeMessage({ id: 'm-agent', role: 'agent', content: 'Hello!', parent_id: 'u-new' }),
              makeMessage({ id: 'u-new', role: 'user', content: 'hi there' }),
              seedUser,
            ],
            next_cursor: null,
          },
        });
      }
      if (url.includes('/messages') && method === 'POST') {
        return fakeResponse({
          body: { data: { message_id: 'u-new', generation_id: 'gen-1', stream_url: 'http://s/stream' } },
        });
      }
      return null;
    });
    vi.stubGlobal(
      'fetch',
      sseFetch([
        'id: 1\nevent: message.created\ndata: {"message_id":"m-agent","role":"agent","generation_status":"streaming"}\n\n',
        'id: 2\nevent: message.delta\ndata: {"message_id":"m-agent","delta":"Hello!"}\n\n',
        'id: 3\nevent: message.done\ndata: {"message_id":"m-agent","generation_status":"done"}\n\n',
      ]),
    );

    renderWithProviders(
      <ConversationPanel client={client} workspaceId="ws-1" session={makeSession()} locale="en" onSessionUpdated={vi.fn()} />,
    );
    await screen.findByTestId('chat-message-u-seed');

    const input = screen.getByTestId('chat-composer-input');
    fireEvent.change(input, { target: { value: 'hi there' } });
    fireEvent.keyDown(input, { key: 'Enter', metaKey: true });

    // 乐观用户消息即时出现(发送后 id 换为服务端 u-new)
    expect(await screen.findByTestId('chat-message-u-new')).toBeInTheDocument();
    // 流式 agent 回复最终落库(终态并入 / 重拉)
    const agentBubble = await screen.findByTestId('chat-message-m-agent');
    expect(agentBubble.textContent).toContain('Hello!');
  });

  it('发送失败回滚乐观消息并 toast', async () => {
    const seedUser = makeMessage({ id: 'u-seed', role: 'user', content: 'seed' });
    const client = routedClient((url, method) => {
      if (url.includes('/messages') && method === 'GET') {
        return fakeResponse({ body: { data: [seedUser], next_cursor: null } });
      }
      if (url.includes('/messages') && method === 'POST') {
        return fakeResponse({ status: 409, body: { error: { code: 'generation_in_progress', message: 'busy' } } });
      }
      return null;
    });
    renderWithProviders(
      <ConversationPanel client={client} workspaceId="ws-1" session={makeSession()} locale="en" onSessionUpdated={vi.fn()} />,
    );
    await screen.findByTestId('chat-message-u-seed');
    const input = screen.getByTestId('chat-composer-input');
    fireEvent.change(input, { target: { value: 'will fail' } });
    fireEvent.keyDown(input, { key: 'Enter', metaKey: true });
    // 失败路径完成:composer 呈现内联错误并保留草稿
    expect(await screen.findByTestId('chat-composer-error')).toBeInTheDocument();
    expect((input as HTMLTextAreaElement).value).toBe('will fail');
    // 回滚:乐观用户消息被移除,仅剩 seed
    await waitFor(() =>
      expect(screen.getAllByTestId(/^chat-message-/).map((e) => e.getAttribute('data-testid'))).toEqual([
        'chat-message-u-seed',
      ]),
    );
  });
});
