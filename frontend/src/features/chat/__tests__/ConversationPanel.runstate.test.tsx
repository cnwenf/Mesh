/**
 * ConversationPanel 运行反馈五态 + 返回入口测试(design-quality §9.8 / §8.3):
 * 流式 queued → running 迁移(data-state,不断言翻译文案)、失败气泡五态徽标、
 * 中断态不徽标、空闲无徽标、手机返回入口导航 /chat。
 */
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { useLocation } from 'react-router';
import { Route, Routes } from 'react-router';
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

function routedClient(router: (url: string, method: string) => Response | null): MeshApiClient {
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    const response = router(url, method);
    if (response !== null) return response;
    return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'x' } } });
  }) as typeof fetch;
  return new MeshApiClient({ baseUrl: 'http://api', getToken: () => null, fetchImpl });
}

function LocationProbe(): React.JSX.Element {
  const location = useLocation();
  return <span data-testid="location-probe">{location.pathname}</span>;
}

/** 参数路由包裹:返回入口经 navigate('/chat') 切换,探针断言落点。 */
function renderPanel(panel: React.JSX.Element, route = '/chat/sess-1') {
  return renderWithProviders(
    <Routes>
      <Route path="/chat" element={<LocationProbe />} />
      <Route path="/chat/:sessionId" element={panel} />
    </Routes>,
    { route },
  );
}

function panelProps(overrides: Partial<ChatSession> = {}) {
  const client = routedClient((url) =>
    url.includes('/messages') ? fakeResponse({ body: { data: [], next_cursor: null } }) : null,
  );
  return {
    client,
    workspaceId: 'ws-1',
    session: makeSession(overrides),
    locale: 'en',
    onSessionUpdated: vi.fn(),
  };
}

afterEach(() => vi.unstubAllGlobals());

describe('ConversationPanel 运行五态与返回(§9.8 / §8.3)', () => {
  it('返回入口呈现,点击导航 /chat', async () => {
    renderPanel(<ConversationPanel {...panelProps()} />);
    fireEvent.click(await screen.findByTestId('chat-back'));
    expect(await screen.findByTestId('location-probe')).toHaveTextContent('/chat');
  });

  it('空闲态无运行徽标', async () => {
    renderPanel(<ConversationPanel {...panelProps()} />);
    await screen.findByTestId('chat-conversation');
    expect(screen.queryByTestId('chat-run-state')).toBeNull();
  });

  it('流式 queued → running:无内容已排队,内容到达运行中', async () => {
    const seed = makeMessage({ id: 'u-seed', role: 'user', content: 'seed' });
    const client = routedClient((url, method) => {
      if (url.includes('/messages') && method === 'GET')
        return fakeResponse({ body: { data: [seed], next_cursor: null } });
      if (url.includes('/messages') && method === 'POST')
        return fakeResponse({
          body: {
            data: { message_id: 'u-new', generation_id: 'gen-q', stream_url: 'http://s/stream' },
          },
        });
      return null;
    });
    let controller: ReadableStreamDefaultController<Uint8Array> | undefined;
    vi.stubGlobal(
      'fetch',
      (async () => ({
        ok: true,
        status: 200,
        body: new ReadableStream<Uint8Array>({
          start(c) {
            controller = c;
            c.enqueue(
              encoder.encode(
                'id: 1\nevent: message.created\ndata: {"message_id":"m-a","role":"agent","generation_status":"streaming"}\n\n',
              ),
            );
          },
        }),
        headers: { get: () => null },
      })) as unknown as typeof fetch,
    );
    renderPanel(
      <ConversationPanel
        client={client}
        workspaceId="ws-1"
        session={makeSession()}
        locale="en"
        onSessionUpdated={vi.fn()}
      />,
    );
    await screen.findByTestId('chat-message-u-seed');
    const input = screen.getByTestId('chat-composer-input');
    fireEvent.change(input, { target: { value: 'go' } });
    fireEvent.keyDown(input, { key: 'Enter', metaKey: true });
    // created 帧到达、尚无 delta → queued
    const badge = await screen.findByTestId('chat-run-state');
    await waitFor(() => expect(badge.querySelector('[data-state="queued"]')).not.toBeNull());
    // delta 帧到达 → running
    act(() => {
      controller?.enqueue(
        encoder.encode(
          'id: 2\nevent: message.delta\ndata: {"message_id":"m-a","delta":"typing..."}\n\n',
        ),
      );
    });
    await waitFor(() => expect(badge.querySelector('[data-state="running"]')).not.toBeNull());
  });

  it('失败 agent 气泡头部呈现五态徽标(failed)', async () => {
    const failed = makeMessage({
      id: 'm-f',
      role: 'agent',
      generation_status: 'failed',
      error_message: 'boom',
    });
    const client = routedClient((url) =>
      url.includes('/messages')
        ? fakeResponse({ body: { data: [failed], next_cursor: null } })
        : null,
    );
    renderPanel(
      <ConversationPanel
        client={client}
        workspaceId="ws-1"
        session={makeSession()}
        locale="en"
        onSessionUpdated={vi.fn()}
      />,
    );
    const bubble = await screen.findByTestId('chat-message-m-f');
    expect(bubble.querySelector('[data-state="failed"]')).not.toBeNull();
    // 原 role=alert 错误段落保留
    expect(screen.getByTestId('chat-error-m-f')).toHaveTextContent('boom');
  });

  it('中断 agent 气泡不呈现五态徽标(维持弱化提示)', async () => {
    const interrupted = makeMessage({ id: 'm-i', role: 'agent', generation_status: 'interrupted' });
    const client = routedClient((url) =>
      url.includes('/messages')
        ? fakeResponse({ body: { data: [interrupted], next_cursor: null } })
        : null,
    );
    renderPanel(
      <ConversationPanel
        client={client}
        workspaceId="ws-1"
        session={makeSession()}
        locale="en"
        onSessionUpdated={vi.fn()}
      />,
    );
    const bubble = await screen.findByTestId('chat-message-m-i');
    expect(bubble.querySelector('[data-state]')).toBeNull();
    expect(screen.getByTestId('chat-interrupted-m-i')).toBeInTheDocument();
  });
});
