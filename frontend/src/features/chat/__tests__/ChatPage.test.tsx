/**
 * ChatPage 核心测试(chat-session.md §4.1):工作区解析(成功/失败/无成员/加载中)、
 * 会话列表 + agent 名册加载、选中会话渲染 ConversationPanel、favorites 真源校正。
 * ChatPage 自建 client(全局 fetch),故以 URL 路由桩 stubGlobal('fetch')。
 */
import { screen } from '@testing-library/react';
import { fireEvent } from '@testing-library/react';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { ChatPage } from '../ChatPage';

/** 选中态路由化:ChatPage 经 useParams 读取 :sessionId,测试须经 Routes 提供匹配。 */
function renderChatPage(route = '/chat') {
  return renderWithProviders(
    <Routes>
      <Route path="/chat" element={<ChatPage />} />
      <Route path="/chat/:sessionId" element={<ChatPage />} />
    </Routes>,
    { route },
  );
}

const me = { memberships: [{ workspace_id: 'ws-1' }] };
const agent = { id: 'a-1', display_name: 'Builder', name: 'builder' };
const session = {
  id: 'sess-1',
  workspace_id: 'ws-1',
  owner_id: 'u-1',
  agent_id: 'a-1',
  agent: { id: 'a-1', name: 'Bot', avatar_url: null },
  title: 'First chat',
  title_is_auto: true,
  context_issue_id: null,
  context_project_id: null,
  status: 'active',
  pinned: false,
  last_message_at: '2026-07-01T00:00:00Z',
  last_message_preview: 'hi',
  message_count: 1,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
};

interface Recorder {
  calls: { url: string; method: string }[];
}

function stubApi(
  router: (url: string, method: string) => Response | null,
  recorder?: Recorder,
): void {
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    recorder?.calls.push({ url, method });
    const response = router(url, method);
    if (response !== null) return response;
    return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'x' } } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', fetchImpl);
}

/** 默认全量桩:me + agents + sessions + favorites + messages。 */
function defaultRouter(overrides: { sessions?: unknown[]; favorites?: unknown[] } = {}) {
  return (url: string, method: string): Response | null => {
    if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
    if (url.includes('/workspaces/ws-1/agents'))
      return fakeResponse({ body: { data: [agent], next_cursor: null } });
    if (url.includes('/chat-sessions') && url.includes('/messages'))
      return fakeResponse({ body: { data: [], next_cursor: null } });
    if (url.includes('/chat-sessions') && method === 'GET')
      return fakeResponse({ body: { data: overrides.sessions ?? [session], next_cursor: null } });
    if (url.includes('/favorites') && method === 'GET')
      return fakeResponse({ body: { data: overrides.favorites ?? [], next_cursor: null } });
    return null;
  };
}

afterEach(() => vi.unstubAllGlobals());

describe('ChatPage(§4.1)', () => {
  it('解析工作区并加载会话列表 + agent 名册', async () => {
    stubApi(defaultRouter());
    renderChatPage();
    expect(await screen.findByTestId('chat-session-sess-1')).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1, name: 'Chat sessions' })).toHaveClass('sr-only');
    // agent 过滤下拉含 Builder
    expect(screen.getByText('Builder')).toBeInTheDocument();
  });

  it('选中会话渲染 ConversationPanel', async () => {
    stubApi(defaultRouter());
    renderChatPage();
    fireEvent.click(await screen.findByText('First chat'));
    expect(await screen.findByTestId('chat-conversation')).toBeInTheDocument();
    expect(await screen.findByText('No messages yet')).toBeInTheDocument();
  });

  it('favorites 真源覆写请求方 pinned 快照', async () => {
    stubApi(defaultRouter({ favorites: [{ target_type: 'chat_session', target_id: 'sess-1' }] }));
    renderChatPage();
    await screen.findByTestId('chat-session-sess-1');
    // 被收藏 → 置顶分组出现
    expect(await screen.findByText('Pinned')).toBeInTheDocument();
    expect(screen.getByTestId('chat-session-pin-sess-1').getAttribute('data-pinned')).toBe('true');
  });

  it('工作区解析失败呈现错误态', async () => {
    stubApi((url) =>
      url.includes('/users/me')
        ? fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } })
        : null,
    );
    renderChatPage();
    expect(await screen.findByText('Something went wrong')).toBeInTheDocument();
  });

  it('无成员身份呈现错误态', async () => {
    stubApi((url) =>
      url.includes('/users/me') ? fakeResponse({ body: { data: { memberships: [] } } }) : null,
    );
    renderChatPage();
    expect(await screen.findByText('Something went wrong')).toBeInTheDocument();
  });

  it('工作区加载中呈现骨架', () => {
    // /users/me 永不 settle → 持续 loading
    vi.stubGlobal(
      'fetch',
      (() => new Promise<Response>(() => undefined)) as unknown as typeof fetch,
    );
    renderChatPage();
    expect(screen.getByText('Loading…')).toBeInTheDocument();
  });

  it('会话列表加载失败呈现错误态并可重试', async () => {
    let sessionCalls = 0;
    stubApi((url, method) => {
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (url.includes('/workspaces/ws-1/agents'))
        return fakeResponse({ body: { data: [agent], next_cursor: null } });
      if (url.includes('/favorites'))
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/chat-sessions') && method === 'GET') {
        sessionCalls += 1;
        return sessionCalls === 1
          ? fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'x' } } })
          : fakeResponse({ body: { data: [session], next_cursor: null } });
      }
      return null;
    });
    renderChatPage();
    expect(await screen.findByText('Something went wrong')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /retry/i }));
    expect(await screen.findByTestId('chat-session-sess-1')).toBeInTheDocument();
  });
});
