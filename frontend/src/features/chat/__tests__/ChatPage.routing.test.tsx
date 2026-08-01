/**
 * ChatPage 路由化选中测试(design-quality §4.4 Conversation 模板):
 * URL 参数 :sessionId 驱动选中(桌面同步 / 手机单栏 activePane)、点选导航、
 * 深链引导(getChatSession 一次性拉取;404 回退占位不崩)、返回列表占位、
 * 新建会话导航落地。
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { useLocation } from 'react-router';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { ChatPage } from '../ChatPage';

const me = { memberships: [{ workspace_id: 'ws-1' }] };
const agent = { id: 'a-1', display_name: 'Builder', name: 'builder' };

function makeSession(id: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    workspace_id: 'ws-1',
    owner_id: 'u-1',
    agent_id: 'a-1',
    agent: { id: 'a-1', name: 'Bot', avatar_url: null },
    title: `Chat ${id}`,
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
    ...overrides,
  };
}

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

function LocationProbe(): React.JSX.Element {
  const location = useLocation();
  return <span data-testid="location-probe">{location.pathname}</span>;
}

function chatRoutes(): React.JSX.Element {
  return (
    <Routes>
      <Route
        path="/chat"
        element={
          <>
            <LocationProbe />
            <ChatPage />
          </>
        }
      />
      <Route
        path="/chat/:sessionId"
        element={
          <>
            <LocationProbe />
            <ChatPage />
          </>
        }
      />
    </Routes>
  );
}

function renderChatPage(route: string) {
  return renderWithProviders(chatRoutes(), { route });
}

/** 基础路由:me + agents + favorites + 会话列表([sess-1, sess-2])+ 消息空页。 */
function baseRouter(sessions: unknown[] = [makeSession('sess-1'), makeSession('sess-2')]) {
  return (url: string, method: string): Response | null => {
    if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
    if (url.includes('/agents'))
      return fakeResponse({ body: { data: [agent], next_cursor: null } });
    if (url.includes('/favorites') && method === 'GET')
      return fakeResponse({ body: { data: [], next_cursor: null } });
    if (url.includes('/messages')) return fakeResponse({ body: { data: [], next_cursor: null } });
    if (url.includes('/chat-sessions') && method === 'GET')
      return fakeResponse({ body: { data: sessions, next_cursor: null } });
    return null;
  };
}

afterEach(() => vi.unstubAllGlobals());

describe('ChatPage 路由化选中(§4.4)', () => {
  it('URL 参数驱动选中:深链列表命中 → 会话面板 + activePane=detail', async () => {
    stubApi(baseRouter());
    const { container } = renderChatPage('/chat/sess-2');
    expect(await screen.findByTestId('chat-conversation')).toBeInTheDocument();
    expect(screen.getByTestId('chat-conversation-title')).toHaveTextContent('Chat sess-2');
    // 手机单栏窗格切换锚点(桌面恒双栏)
    expect(
      container.querySelector('.mesh-conversation-layout')?.getAttribute('data-active-pane'),
    ).toBe('detail');
  });

  it('无参数时呈现占位 + activePane=list', async () => {
    stubApi(baseRouter());
    const { container } = renderChatPage('/chat');
    await screen.findByTestId('chat-session-sess-1');
    expect(screen.queryByTestId('chat-conversation')).toBeNull();
    expect(screen.getByTestId('location-probe')).toHaveTextContent('/chat');
    expect(
      container.querySelector('.mesh-conversation-layout')?.getAttribute('data-active-pane'),
    ).toBe('list');
  });

  it('点选会话 → 导航 /chat/:id 并渲染会话面板', async () => {
    stubApi(baseRouter());
    renderChatPage('/chat');
    fireEvent.click(await screen.findByText('Chat sess-1'));
    await waitFor(() =>
      expect(screen.getByTestId('location-probe')).toHaveTextContent('/chat/sess-1'),
    );
    expect(await screen.findByTestId('chat-conversation')).toBeInTheDocument();
    // 列表选中态同步(派生 selectedId)
    expect(screen.getByTestId('chat-session-sess-1').getAttribute('data-selected')).toBe('true');
  });

  it('深链引导:参数不在列表 → getChatSession 拉取并渲染', async () => {
    const recorder: Recorder = { calls: [] };
    const deep = makeSession('deep-1', { title: 'Deep linked' });
    stubApi((url, method) => {
      // 单会话拉取须先于列表匹配(路径同含 /chat-sessions)。
      if (url.includes('/chat-sessions/deep-1') && !url.includes('/messages') && method === 'GET')
        return fakeResponse({ body: { data: deep } });
      return baseRouter()(url, method);
    }, recorder);
    renderChatPage('/chat/deep-1');
    expect(await screen.findByTestId('chat-conversation')).toBeInTheDocument();
    expect(screen.getByTestId('chat-conversation-title')).toHaveTextContent('Deep linked');
    // 单会话仅拉一次(去重锚点;排除会话面板自身的 messages 拉取)
    expect(
      recorder.calls.filter(
        (c) =>
          c.url.includes('/chat-sessions/deep-1') &&
          !c.url.includes('/messages') &&
          c.method === 'GET',
      ),
    ).toHaveLength(1);
  });

  it('深链 404 → 回退占位(不崩、无会话面板)', async () => {
    stubApi(baseRouter());
    renderChatPage('/chat/ghost');
    await screen.findByTestId('chat-session-sess-1');
    // H6:引导 404 → 确证不存在后 replace 回 /chat,最终呈现列表占位(无会话面板)。
    // 重定向经 effect 异步发生,故 waitFor 占位文案(中间可能短暂 Skeleton)。
    expect(screen.queryByTestId('chat-conversation')).toBeNull();
    await screen.findByText('Select a conversation');
  });

  it('返回列表(参数归无)→ 清空选中呈现占位', async () => {
    stubApi(baseRouter());
    renderChatPage('/chat/sess-1');
    await screen.findByTestId('chat-conversation');
    // ConversationPanel 头部返回入口 → /chat
    fireEvent.click(screen.getByTestId('chat-back'));
    await waitFor(() => expect(screen.getByTestId('location-probe')).toHaveTextContent('/chat'));
    await waitFor(() => expect(screen.queryByTestId('chat-conversation')).toBeNull());
  });

  it('新建会话 → 导航 /chat/:newId 并经引导快照即时渲染', async () => {
    const recorder: Recorder = { calls: [] };
    const created = makeSession('sess-9', { title: 'Brand new' });
    stubApi((url, method) => {
      if (url.includes('/chat-sessions') && method === 'POST')
        return fakeResponse({ status: 201, body: { data: created } });
      return baseRouter()(url, method);
    }, recorder);
    renderChatPage('/chat');
    await screen.findByTestId('chat-session-sess-1');
    fireEvent.click(screen.getByTestId('chat-new-session'));
    await screen.findByTestId('chat-new-session-agent');
    fireEvent.change(screen.getByTestId('chat-new-session-agent'), { target: { value: 'a-1' } });
    fireEvent.click(screen.getByTestId('chat-new-session-create'));
    await waitFor(() =>
      expect(screen.getByTestId('location-probe')).toHaveTextContent('/chat/sess-9'),
    );
    expect(await screen.findByTestId('chat-conversation')).toBeInTheDocument();
    expect(screen.getByTestId('chat-conversation-title')).toHaveTextContent('Brand new');
  });

  it('引导快照按 id 守卫:深链 A → 点选列表 B → 返回占位', async () => {
    const deepA = makeSession('deep-a', { title: 'First deep' });
    stubApi((url, method) => {
      if (url.includes('/chat-sessions/deep-a') && !url.includes('/messages') && method === 'GET')
        return fakeResponse({ body: { data: deepA } });
      return baseRouter()(url, method);
    });
    renderChatPage('/chat/deep-a');
    // 引导会话渲染
    expect(await screen.findByTestId('chat-conversation-title')).toHaveTextContent('First deep');
    // 点选列表内会话 → 列表命中优先(引导快照 id 不匹配不泄漏)
    fireEvent.click(screen.getByText('Chat sess-1'));
    await waitFor(() =>
      expect(screen.getByTestId('chat-conversation-title')).toHaveTextContent('Chat sess-1'),
    );
    expect(screen.getByTestId('location-probe')).toHaveTextContent('/chat/sess-1');
    // 返回 /chat → 占位(参数归无清空选中)
    fireEvent.click(screen.getByTestId('chat-back'));
    await waitFor(() => expect(screen.queryByTestId('chat-conversation')).toBeNull());
    expect(screen.getByTestId('location-probe')).toHaveTextContent('/chat');
  });
});
