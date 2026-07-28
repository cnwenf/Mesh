/**
 * ChatPage 分支覆盖测试(chat-session.md §4.1):置顶乐观切换(成功/失败回滚)、
 * 新建会话全流程(对话框 → 创建 → 选中)、agent 过滤重载。
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { RealtimeContext } from '../../../shell/AppShell';
import type { RealtimeContextValue } from '../../../shell/AppShell';
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

afterEach(() => vi.unstubAllGlobals());

describe('ChatPage 分支覆盖(§4.1)', () => {
  it('置顶成功:PUT favorites + 乐观移入置顶分组', async () => {
    const recorder: Recorder = { calls: [] };
    stubApi((url, method) => {
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (url.includes('/agents'))
        return fakeResponse({ body: { data: [agent], next_cursor: null } });
      if (url.includes('/favorites') && method === 'GET')
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/favorites/chat_session/sess-1') && method === 'PUT')
        return fakeResponse({ status: 201, body: { data: {} } });
      if (url.includes('/chat-sessions') && method === 'GET')
        return fakeResponse({ body: { data: [makeSession('sess-1')], next_cursor: null } });
      return null;
    }, recorder);
    renderWithProviders(<ChatPage />);
    await screen.findByTestId('chat-session-sess-1');
    fireEvent.click(screen.getByTestId('chat-session-pin-sess-1'));
    await waitFor(() =>
      expect(
        recorder.calls.some(
          (c) => c.method === 'PUT' && c.url.includes('/favorites/chat_session/sess-1'),
        ),
      ).toBe(true),
    );
    expect(await screen.findByText('Pinned')).toBeInTheDocument();
    expect(screen.getByTestId('chat-session-pin-sess-1').getAttribute('data-pinned')).toBe('true');
  });

  it('置顶失败:回滚 + toast', async () => {
    const recorder: Recorder = { calls: [] };
    stubApi((url, method) => {
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (url.includes('/agents'))
        return fakeResponse({ body: { data: [agent], next_cursor: null } });
      if (url.includes('/favorites') && method === 'GET')
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/favorites/chat_session/sess-1') && method === 'PUT')
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        });
      if (url.includes('/chat-sessions') && method === 'GET')
        return fakeResponse({ body: { data: [makeSession('sess-1')], next_cursor: null } });
      return null;
    }, recorder);
    renderWithProviders(<ChatPage />);
    await screen.findByTestId('chat-session-sess-1');
    fireEvent.click(screen.getByTestId('chat-session-pin-sess-1'));
    await waitFor(() => expect(document.querySelector('.mesh-toast')).not.toBeNull());
    // 回滚:仍未置顶
    expect(screen.getByTestId('chat-session-pin-sess-1').getAttribute('data-pinned')).toBe('false');
  });

  it('新建会话全流程:对话框选 agent → 创建 → 选中渲染', async () => {
    const recorder: Recorder = { calls: [] };
    const created = makeSession('sess-2', { title: 'Brand new' });
    stubApi((url, method) => {
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (url.includes('/agents'))
        return fakeResponse({ body: { data: [agent], next_cursor: null } });
      if (url.includes('/favorites') && method === 'GET')
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/chat-sessions') && method === 'POST')
        return fakeResponse({ status: 201, body: { data: created } });
      if (url.includes('/chat-sessions/sess-2/messages'))
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/chat-sessions') && method === 'GET')
        return fakeResponse({ body: { data: [makeSession('sess-1')], next_cursor: null } });
      return null;
    }, recorder);
    renderWithProviders(<ChatPage />);
    await screen.findByTestId('chat-session-sess-1');
    fireEvent.click(screen.getByTestId('chat-new-session'));
    // 对话框加载 agent 后选择并创建
    await screen.findByTestId('chat-new-session-agent');
    fireEvent.change(screen.getByTestId('chat-new-session-agent'), { target: { value: 'a-1' } });
    fireEvent.click(screen.getByTestId('chat-new-session-create'));
    await waitFor(() =>
      expect(
        recorder.calls.some((c) => c.method === 'POST' && c.url.includes('/chat-sessions')),
      ).toBe(true),
    );
    // 新建会话被选中并渲染会话面板
    expect(await screen.findByTestId('chat-conversation')).toBeInTheDocument();
    expect(screen.getByTestId('chat-conversation-title')).toHaveTextContent('Brand new');
  });

  it('agent 过滤变更触发带 agent_id 的重载', async () => {
    const recorder: Recorder = { calls: [] };
    stubApi((url, method) => {
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (url.includes('/agents'))
        return fakeResponse({ body: { data: [agent], next_cursor: null } });
      if (url.includes('/favorites') && method === 'GET')
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/chat-sessions') && method === 'GET')
        return fakeResponse({ body: { data: [makeSession('sess-1')], next_cursor: null } });
      return null;
    }, recorder);
    renderWithProviders(<ChatPage />);
    await screen.findByTestId('chat-session-sess-1');
    fireEvent.change(screen.getByTestId('chat-filter-agent'), { target: { value: 'a-1' } });
    await waitFor(() =>
      expect(
        recorder.calls.some(
          (c) => c.url.includes('/chat-sessions') && c.url.includes('agent_id=a-1'),
        ),
      ).toBe(true),
    );
  });

  it('状态过滤变更为 archived 触发重载', async () => {
    const recorder: Recorder = { calls: [] };
    stubApi((url, method) => {
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (url.includes('/agents'))
        return fakeResponse({ body: { data: [agent], next_cursor: null } });
      if (url.includes('/favorites') && method === 'GET')
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/chat-sessions') && method === 'GET')
        return fakeResponse({ body: { data: [], next_cursor: null } });
      return null;
    }, recorder);
    renderWithProviders(<ChatPage />);
    await screen.findByTestId('chat-session-panel');
    fireEvent.change(screen.getByTestId('chat-filter-status'), { target: { value: 'archived' } });
    await waitFor(() =>
      expect(
        recorder.calls.some(
          (c) => c.url.includes('/chat-sessions') && c.url.includes('status=archived'),
        ),
      ).toBe(true),
    );
  });

  it('取消置顶:DELETE favorites(已置顶会话)', async () => {
    const recorder: Recorder = { calls: [] };
    stubApi((url, method) => {
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (url.includes('/agents'))
        return fakeResponse({ body: { data: [agent], next_cursor: null } });
      if (url.includes('/favorites') && method === 'GET')
        return fakeResponse({
          body: { data: [{ target_type: 'chat_session', target_id: 'sess-1' }], next_cursor: null },
        });
      if (url.includes('/favorites/chat_session/sess-1') && method === 'DELETE')
        return fakeResponse({ status: 204 });
      if (url.includes('/chat-sessions') && method === 'GET')
        return fakeResponse({
          body: { data: [makeSession('sess-1', { pinned: true })], next_cursor: null },
        });
      return null;
    }, recorder);
    renderWithProviders(<ChatPage />);
    await screen.findByTestId('chat-session-sess-1');
    fireEvent.click(screen.getByTestId('chat-session-pin-sess-1'));
    await waitFor(() =>
      expect(
        recorder.calls.some(
          (c) => c.method === 'DELETE' && c.url.includes('/favorites/chat_session/sess-1'),
        ),
      ).toBe(true),
    );
  });

  it('WS 列表频道帧并入会话预览(订阅 + 合并 + 卸载清理)', async () => {
    type Listener = (frame: unknown) => void;
    const listeners = new Set<Listener>();
    const fakeRealtime = {
      state: 'connected',
      client: {
        subscribe: vi.fn(),
        unsubscribe: vi.fn(),
        onFrame: (cb: Listener) => {
          listeners.add(cb);
          return () => listeners.delete(cb);
        },
      },
    } as unknown as RealtimeContextValue;
    stubApi((url, method) => {
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (url.includes('/agents'))
        return fakeResponse({ body: { data: [agent], next_cursor: null } });
      if (url.includes('/favorites') && method === 'GET')
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/chat-sessions') && method === 'GET')
        return fakeResponse({ body: { data: [makeSession('sess-1')], next_cursor: null } });
      return null;
    });
    const { unmount } = renderWithProviders(
      <RealtimeContext.Provider value={fakeRealtime}>
        <ChatPage />
      </RealtimeContext.Provider>,
    );
    await screen.findByTestId('chat-session-sess-1');
    // 订阅在会话加载后于 effect 内异步建立,waitFor 兜底其建立时机
    await waitFor(() =>
      expect(fakeRealtime.client.subscribe).toHaveBeenCalledWith('chat_list:u-1'),
    );
    // 推送 message.done 帧 → 预览并入
    for (const cb of listeners)
      cb({
        op: 'event',
        channel: 'chat_list:u-1',
        seq: 1,
        event: 'message.done',
        payload: {
          session_id: 'sess-1',
          last_message_preview: 'updated preview',
          last_message_at: '2026-07-02T00:00:00Z',
          message_count: 5,
        },
      });
    expect(await screen.findByTestId('chat-session-preview-sess-1')).toHaveTextContent(
      'updated preview',
    );
    // 卸载触发 unsubscribe(清理路径)
    unmount();
    expect(fakeRealtime.client.unsubscribe).toHaveBeenCalledWith('chat_list:u-1');
  });

  it('WS 帧频道不匹配时忽略(不并入预览)', async () => {
    type Listener = (frame: unknown) => void;
    const listeners = new Set<Listener>();
    const fakeRealtime = {
      state: 'connected',
      client: {
        subscribe: vi.fn(),
        unsubscribe: vi.fn(),
        onFrame: (cb: Listener) => {
          listeners.add(cb);
          return () => listeners.delete(cb);
        },
      },
    } as unknown as RealtimeContextValue;
    stubApi((url, method) => {
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (url.includes('/agents'))
        return fakeResponse({ body: { data: [agent], next_cursor: null } });
      if (url.includes('/favorites') && method === 'GET')
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/chat-sessions') && method === 'GET')
        return fakeResponse({
          body: {
            data: [makeSession('sess-1', { last_message_preview: 'orig' })],
            next_cursor: null,
          },
        });
      return null;
    });
    renderWithProviders(
      <RealtimeContext.Provider value={fakeRealtime}>
        <ChatPage />
      </RealtimeContext.Provider>,
    );
    await screen.findByTestId('chat-session-preview-sess-1');
    for (const cb of listeners)
      cb({
        op: 'event',
        channel: 'other:channel',
        seq: 1,
        event: 'message.done',
        payload: { session_id: 'sess-1', last_message_preview: 'CHANGED' },
      });
    // 频道不匹配 → 预览保持原值
    expect(screen.getByTestId('chat-session-preview-sess-1')).toHaveTextContent('orig');
  });

  it('新建对话框取消(onClose 关闭)', async () => {
    stubApi((url, method) => {
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (url.includes('/agents'))
        return fakeResponse({ body: { data: [agent], next_cursor: null } });
      if (url.includes('/favorites') && method === 'GET')
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/chat-sessions') && method === 'GET')
        return fakeResponse({ body: { data: [makeSession('sess-1')], next_cursor: null } });
      return null;
    });
    renderWithProviders(<ChatPage />);
    await screen.findByTestId('chat-session-sess-1');
    fireEvent.click(screen.getByTestId('chat-new-session'));
    expect(await screen.findByTestId('chat-new-session-agent')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('chat-new-session-cancel'));
    await waitFor(() => expect(screen.queryByTestId('chat-new-session-agent')).toBeNull());
  });

  it('favorites 拉取失败:沿用服务端 pinned 快照(不阻断列表)', async () => {
    stubApi((url, method) => {
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (url.includes('/agents'))
        return fakeResponse({ body: { data: [agent], next_cursor: null } });
      if (url.includes('/favorites') && method === 'GET')
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        });
      if (url.includes('/chat-sessions') && method === 'GET')
        return fakeResponse({
          body: { data: [makeSession('sess-1', { pinned: false })], next_cursor: null },
        });
      return null;
    });
    renderWithProviders(<ChatPage />);
    expect(await screen.findByTestId('chat-session-sess-1')).toBeInTheDocument();
    // favorites 失败 → 无置顶分组
    expect(screen.queryByText('Pinned')).toBeNull();
  });

  it('选中会话后置顶(乐观 map/setSelected 匹配分支)', async () => {
    const recorder: Recorder = { calls: [] };
    stubApi((url, method) => {
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (url.includes('/agents'))
        return fakeResponse({ body: { data: [agent], next_cursor: null } });
      if (url.includes('/favorites') && method === 'GET')
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/favorites/chat_session/sess-1') && method === 'PUT')
        return fakeResponse({ status: 201, body: { data: {} } });
      if (url.includes('/chat-sessions/sess-1/messages'))
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/chat-sessions') && method === 'GET')
        return fakeResponse({
          body: { data: [makeSession('sess-1'), makeSession('sess-2')], next_cursor: null },
        });
      return null;
    }, recorder);
    renderWithProviders(<ChatPage />);
    await screen.findByTestId('chat-session-sess-1');
    // 先选中 sess-1,再置顶 → setSelected 匹配分支
    fireEvent.click(screen.getByText('Chat sess-1'));
    await screen.findByTestId('chat-conversation');
    fireEvent.click(screen.getByTestId('chat-session-pin-sess-1'));
    await waitFor(() =>
      expect(
        recorder.calls.some(
          (c) => c.method === 'PUT' && c.url.includes('/favorites/chat_session/sess-1'),
        ),
      ).toBe(true),
    );
    // sess-2 仍在(未匹配项保留),sess-1 进入置顶分组
    expect(screen.getByTestId('chat-session-sess-2')).toBeInTheDocument();
    expect(await screen.findByText('Pinned')).toBeInTheDocument();
  });

  it('选中会话后置顶失败回滚(回滚 map/setSelected 匹配分支)', async () => {
    stubApi((url, method) => {
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (url.includes('/agents'))
        return fakeResponse({ body: { data: [agent], next_cursor: null } });
      if (url.includes('/favorites') && method === 'GET')
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/favorites/chat_session/sess-1') && method === 'PUT')
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        });
      if (url.includes('/chat-sessions/sess-1/messages'))
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/chat-sessions') && method === 'GET')
        return fakeResponse({
          body: { data: [makeSession('sess-1'), makeSession('sess-2')], next_cursor: null },
        });
      return null;
    });
    renderWithProviders(<ChatPage />);
    await screen.findByTestId('chat-session-sess-1');
    fireEvent.click(screen.getByText('Chat sess-1'));
    await screen.findByTestId('chat-conversation');
    fireEvent.click(screen.getByTestId('chat-session-pin-sess-1'));
    await waitFor(() => expect(document.querySelector('.mesh-toast')).not.toBeNull());
    // 回滚:未置顶,无 Pinned 分组
    expect(screen.getByTestId('chat-session-pin-sess-1').getAttribute('data-pinned')).toBe('false');
    expect(screen.queryByText('Pinned')).toBeNull();
  });
});
