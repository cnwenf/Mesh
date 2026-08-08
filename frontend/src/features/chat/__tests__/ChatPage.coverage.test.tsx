/**
 * ChatPage 分支覆盖测试(chat-session.md §4.1):置顶乐观切换(成功/失败回滚)、
 * 新建会话全流程(对话框 → 创建 → 选中)、agent 过滤重载。
 */
import type { ReactElement } from 'react';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { RealtimeContext } from '../../../shell/AppShell';
import type { RealtimeContextValue } from '../../../shell/AppShell';
import { useShortcutRegistry } from '../../../shortcuts';
import { renderWithProviders } from '../../../test-utils/render';
import { ChatPage } from '../ChatPage';

/** 选中态路由化:ChatPage 经 useParams 读取 :sessionId,测试须经 Routes 提供匹配。 */
function chatRoutes(): ReactElement {
  return (
    <Routes>
      <Route path="/chat" element={<ChatPage />} />
      <Route path="/chat/:sessionId" element={<ChatPage />} />
    </Routes>
  );
}

function renderChatPage(route = '/chat') {
  return renderWithProviders(chatRoutes(), { route });
}

const me = { memberships: [{ workspace_id: 'ws-1' }] };
/** 携带 user 的 /users/me:驱动「当前成员 id」解析 effect(名册匹配)。 */
const meWithUser = {
  user: { id: 'u-1', email: 'me@example.com' },
  memberships: [{ workspace_id: 'ws-1' }],
};
const agent = { id: 'a-1', display_name: 'Builder', name: 'builder' };

function makeMember(overrides: Record<string, unknown> = {}) {
  return {
    id: 'mem-1',
    member_type: 'human',
    role: 'member',
    status: 'active',
    display_name: 'Member',
    joined_at: null,
    profile: null,
    ...overrides,
  };
}

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
      if (url.includes('/chat-sessions') && method === 'GET') {
        if (url.includes('status=archived'))
          return fakeResponse({ body: { data: [], next_cursor: null } });
        return fakeResponse({ body: { data: [makeSession('sess-1')], next_cursor: null } });
      }
      return null;
    }, recorder);
    renderChatPage();
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
      if (url.includes('/chat-sessions') && method === 'GET') {
        if (url.includes('status=archived'))
          return fakeResponse({ body: { data: [], next_cursor: null } });
        return fakeResponse({ body: { data: [makeSession('sess-1')], next_cursor: null } });
      }
      return null;
    }, recorder);
    renderChatPage();
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
      if (url.includes('/chat-sessions') && method === 'GET') {
        if (url.includes('status=archived'))
          return fakeResponse({ body: { data: [], next_cursor: null } });
        return fakeResponse({ body: { data: [makeSession('sess-1')], next_cursor: null } });
      }
      return null;
    }, recorder);
    renderChatPage();
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
      if (url.includes('/chat-sessions') && method === 'GET') {
        if (url.includes('status=archived'))
          return fakeResponse({ body: { data: [], next_cursor: null } });
        return fakeResponse({ body: { data: [makeSession('sess-1')], next_cursor: null } });
      }
      return null;
    }, recorder);
    renderChatPage();
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

  it('会话列表并行拉取 active 与 archived 两页并合并渲染(§4.1)', async () => {
    const recorder: Recorder = { calls: [] };
    stubApi((url, method) => {
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (url.includes('/agents'))
        return fakeResponse({ body: { data: [agent], next_cursor: null } });
      if (url.includes('/favorites') && method === 'GET')
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/chat-sessions') && method === 'GET') {
        if (url.includes('status=archived'))
          return fakeResponse({
            body: { data: [makeSession('sess-arc', { status: 'archived' })], next_cursor: null },
          });
        return fakeResponse({ body: { data: [makeSession('sess-1')], next_cursor: null } });
      }
      return null;
    }, recorder);
    renderChatPage();
    await screen.findByTestId('chat-session-sess-1');
    await screen.findByTestId('chat-session-sess-arc');
    // 两个 status 分页均被请求。
    expect(
      recorder.calls.some(
        (c) => c.url.includes('/chat-sessions') && c.url.includes('status=active'),
      ),
    ).toBe(true);
    expect(
      recorder.calls.some(
        (c) => c.url.includes('/chat-sessions') && c.url.includes('status=archived'),
      ),
    ).toBe(true);
    // 归档会话渲染在底部归档区。
    expect(
      within(screen.getByTestId('chat-sessions-archived')).getByTestId('chat-session-sess-arc'),
    ).toBeInTheDocument();
  });

  it('归档切换:PATCH status + If-Match,会话移入归档区', async () => {
    const recorder: Recorder = { calls: [] };
    stubApi((url, method) => {
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (url.includes('/agents'))
        return fakeResponse({ body: { data: [agent], next_cursor: null } });
      if (url.includes('/favorites') && method === 'GET')
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/chat-sessions/sess-1') && method === 'PATCH')
        return fakeResponse({
          body: {
            data: makeSession('sess-1', {
              status: 'archived',
              updated_at: '2026-07-02T00:00:00Z',
            }),
          },
        });
      if (url.includes('/chat-sessions') && method === 'GET') {
        if (url.includes('status=archived'))
          return fakeResponse({ body: { data: [], next_cursor: null } });
        return fakeResponse({ body: { data: [makeSession('sess-1')], next_cursor: null } });
      }
      return null;
    }, recorder);
    renderChatPage();
    await screen.findByTestId('chat-session-sess-1');
    fireEvent.click(screen.getByTestId('chat-session-archive-sess-1'));
    await waitFor(() =>
      expect(
        recorder.calls.some((c) => c.method === 'PATCH' && c.url.includes('/chat-sessions/sess-1')),
      ).toBe(true),
    );
    expect(
      await within(screen.getByTestId('chat-sessions-archived')).findByTestId(
        'chat-session-sess-1',
      ),
    ).toBeInTheDocument();
  });

  it('归档失败:回滚乐观态 + toast', async () => {
    stubApi((url, method) => {
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (url.includes('/agents'))
        return fakeResponse({ body: { data: [agent], next_cursor: null } });
      if (url.includes('/favorites') && method === 'GET')
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/chat-sessions/sess-1') && method === 'PATCH')
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        });
      if (url.includes('/chat-sessions') && method === 'GET') {
        if (url.includes('status=archived'))
          return fakeResponse({ body: { data: [], next_cursor: null } });
        return fakeResponse({ body: { data: [makeSession('sess-1')], next_cursor: null } });
      }
      return null;
    });
    renderChatPage();
    await screen.findByTestId('chat-session-sess-1');
    fireEvent.click(screen.getByTestId('chat-session-archive-sess-1'));
    await waitFor(() => expect(document.querySelector('.mesh-toast')).not.toBeNull());
    // 回滚:仍在活跃区,无归档分组。
    expect(screen.queryByTestId('chat-sessions-archived')).toBeNull();
    expect(screen.getByTestId('chat-session-sess-1')).toBeInTheDocument();
  });

  it('删除会话:确认后 DELETE 软删并自列表移除', async () => {
    const recorder: Recorder = { calls: [] };
    stubApi((url, method) => {
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (url.includes('/agents'))
        return fakeResponse({ body: { data: [agent], next_cursor: null } });
      if (url.includes('/favorites') && method === 'GET')
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/chat-sessions/sess-1') && method === 'DELETE')
        return fakeResponse({ status: 204 });
      if (url.includes('/chat-sessions') && method === 'GET') {
        if (url.includes('status=archived'))
          return fakeResponse({ body: { data: [], next_cursor: null } });
        return fakeResponse({ body: { data: [makeSession('sess-1')], next_cursor: null } });
      }
      return null;
    }, recorder);
    renderChatPage();
    await screen.findByTestId('chat-session-sess-1');
    fireEvent.click(screen.getByTestId('chat-session-delete-sess-1'));
    fireEvent.click(screen.getByTestId('chat-session-delete-confirm'));
    await waitFor(() =>
      expect(
        recorder.calls.some(
          (c) => c.method === 'DELETE' && c.url.includes('/chat-sessions/sess-1'),
        ),
      ).toBe(true),
    );
    await waitFor(() => expect(screen.queryByTestId('chat-session-sess-1')).toBeNull());
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
      if (url.includes('/chat-sessions') && method === 'GET') {
        if (url.includes('status=archived'))
          return fakeResponse({ body: { data: [], next_cursor: null } });
        return fakeResponse({
          body: { data: [makeSession('sess-1', { pinned: true })], next_cursor: null },
        });
      }
      return null;
    }, recorder);
    renderChatPage();
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
      if (url.includes('/chat-sessions') && method === 'GET') {
        if (url.includes('status=archived'))
          return fakeResponse({ body: { data: [], next_cursor: null } });
        return fakeResponse({ body: { data: [makeSession('sess-1')], next_cursor: null } });
      }
      return null;
    });
    const { unmount } = renderWithProviders(
      <RealtimeContext.Provider value={fakeRealtime}>{chatRoutes()}</RealtimeContext.Provider>,
      { route: '/chat' },
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
      if (url.includes('/chat-sessions') && method === 'GET') {
        if (url.includes('status=archived'))
          return fakeResponse({ body: { data: [], next_cursor: null } });
        return fakeResponse({
          body: {
            data: [makeSession('sess-1', { last_message_preview: 'orig' })],
            next_cursor: null,
          },
        });
      }
      return null;
    });
    renderWithProviders(
      <RealtimeContext.Provider value={fakeRealtime}>{chatRoutes()}</RealtimeContext.Provider>,
      { route: '/chat' },
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
      if (url.includes('/chat-sessions') && method === 'GET') {
        if (url.includes('status=archived'))
          return fakeResponse({ body: { data: [], next_cursor: null } });
        return fakeResponse({ body: { data: [makeSession('sess-1')], next_cursor: null } });
      }
      return null;
    });
    renderChatPage();
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
      if (url.includes('/chat-sessions') && method === 'GET') {
        if (url.includes('status=archived'))
          return fakeResponse({ body: { data: [], next_cursor: null } });
        return fakeResponse({
          body: { data: [makeSession('sess-1', { pinned: false })], next_cursor: null },
        });
      }
      return null;
    });
    renderChatPage();
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
      if (url.includes('/chat-sessions') && method === 'GET') {
        if (url.includes('status=archived'))
          return fakeResponse({ body: { data: [], next_cursor: null } });
        return fakeResponse({
          body: { data: [makeSession('sess-1'), makeSession('sess-2')], next_cursor: null },
        });
      }
      return null;
    }, recorder);
    renderChatPage();
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
      if (url.includes('/chat-sessions') && method === 'GET') {
        if (url.includes('status=archived'))
          return fakeResponse({ body: { data: [], next_cursor: null } });
        return fakeResponse({
          body: { data: [makeSession('sess-1'), makeSession('sess-2')], next_cursor: null },
        });
      }
      return null;
    });
    renderChatPage();
    await screen.findByTestId('chat-session-sess-1');
    fireEvent.click(screen.getByText('Chat sess-1'));
    await screen.findByTestId('chat-conversation');
    fireEvent.click(screen.getByTestId('chat-session-pin-sess-1'));
    await waitFor(() => expect(document.querySelector('.mesh-toast')).not.toBeNull());
    // 回滚:未置顶,无 Pinned 分组
    expect(screen.getByTestId('chat-session-pin-sess-1').getAttribute('data-pinned')).toBe('false');
    expect(screen.queryByText('Pinned')).toBeNull();
  });

  it('当前成员解析:名册命中(email 匹配)→ 订阅本人私有列表频道', async () => {
    const fakeRealtime = {
      state: 'connected',
      client: { subscribe: vi.fn(), unsubscribe: vi.fn(), onFrame: () => () => undefined },
    } as unknown as RealtimeContextValue;
    stubApi((url, method) => {
      if (url.includes('/users/me')) return fakeResponse({ body: { data: meWithUser } });
      if (url.includes('/members') && method === 'GET')
        return fakeResponse({
          body: {
            data: [
              // agent 成员:member_type 分支跳过
              makeMember({
                id: 'mem-agent',
                member_type: 'agent',
                profile: { id: 'a-1', name: 'Bot', description: null, avatar_url: null },
              }),
              // 人类但 profile 为空:profile === null 分支跳过
              makeMember({ id: 'mem-null' }),
              // id 不匹配、email 匹配:命中并返回 member.id
              makeMember({
                id: 'mem-email',
                profile: {
                  id: 'u-9',
                  full_name: 'Me',
                  email: 'me@example.com',
                  avatar_url: null,
                },
              }),
            ],
            next_cursor: null,
          },
        });
      if (url.includes('/agents'))
        return fakeResponse({ body: { data: [agent], next_cursor: null } });
      if (url.includes('/favorites') && method === 'GET')
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/chat-sessions') && method === 'GET') {
        if (url.includes('status=archived'))
          return fakeResponse({ body: { data: [], next_cursor: null } });
        return fakeResponse({ body: { data: [makeSession('sess-1')], next_cursor: null } });
      }
      return null;
    });
    renderWithProviders(
      <RealtimeContext.Provider value={fakeRealtime}>{chatRoutes()}</RealtimeContext.Provider>,
      { route: '/chat' },
    );
    await screen.findByTestId('chat-session-sess-1');
    await waitFor(() =>
      expect(fakeRealtime.client.subscribe).toHaveBeenCalledWith('chat_list:mem-email'),
    );
  });

  it('名册无匹配人类:回退会话 owner_id 兜底订阅', async () => {
    const fakeRealtime = {
      state: 'connected',
      client: { subscribe: vi.fn(), unsubscribe: vi.fn(), onFrame: () => () => undefined },
    } as unknown as RealtimeContextValue;
    stubApi((url, method) => {
      if (url.includes('/users/me'))
        return fakeResponse({
          body: {
            data: { user: { id: 'u-7', email: 'other@example.com' }, memberships: me.memberships },
          },
        });
      if (url.includes('/members') && method === 'GET')
        return fakeResponse({
          body: {
            data: [
              makeMember({
                id: 'mem-other',
                profile: {
                  id: 'u-2',
                  full_name: 'Other',
                  email: 'someone-else@example.com',
                  avatar_url: null,
                },
              }),
            ],
            next_cursor: null,
          },
        });
      if (url.includes('/agents'))
        return fakeResponse({ body: { data: [agent], next_cursor: null } });
      if (url.includes('/favorites') && method === 'GET')
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/chat-sessions') && method === 'GET') {
        if (url.includes('status=archived'))
          return fakeResponse({ body: { data: [], next_cursor: null } });
        return fakeResponse({ body: { data: [makeSession('sess-1')], next_cursor: null } });
      }
      return null;
    });
    renderWithProviders(
      <RealtimeContext.Provider value={fakeRealtime}>{chatRoutes()}</RealtimeContext.Provider>,
      { route: '/chat' },
    );
    await screen.findByTestId('chat-session-sess-1');
    // 名册未命中 → memberId 为 null → 沿用 sessions[0].owner_id 兜底
    await waitFor(() =>
      expect(fakeRealtime.client.subscribe).toHaveBeenCalledWith('chat_list:u-1'),
    );
  });

  it('名册拉取失败且列表为空:memberId 置空、无订阅', async () => {
    const recorder: Recorder = { calls: [] };
    const fakeRealtime = {
      state: 'connected',
      client: { subscribe: vi.fn(), unsubscribe: vi.fn(), onFrame: () => () => undefined },
    } as unknown as RealtimeContextValue;
    stubApi((url, method) => {
      if (url.includes('/users/me')) return fakeResponse({ body: { data: meWithUser } });
      if (url.includes('/members') && method === 'GET')
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        });
      if (url.includes('/agents'))
        return fakeResponse({ body: { data: [agent], next_cursor: null } });
      if (url.includes('/favorites') && method === 'GET')
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/chat-sessions') && method === 'GET') {
        if (url.includes('status=archived'))
          return fakeResponse({ body: { data: [], next_cursor: null } });
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }
      return null;
    }, recorder);
    renderWithProviders(
      <RealtimeContext.Provider value={fakeRealtime}>{chatRoutes()}</RealtimeContext.Provider>,
      { route: '/chat' },
    );
    // 空列表态呈现 + 名册请求确实失败(500)
    expect(await screen.findByText('No conversations yet')).toBeInTheDocument();
    await waitFor(() => expect(recorder.calls.some((c) => c.url.includes('/members'))).toBe(true));
    await waitFor(() => expect(fakeRealtime.client.subscribe).not.toHaveBeenCalled());
  });

  it('agents 名册拉取失败:过滤下拉降级为仅「全部」', async () => {
    stubApi((url, method) => {
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (url.includes('/agents'))
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        });
      if (url.includes('/favorites') && method === 'GET')
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/chat-sessions') && method === 'GET') {
        if (url.includes('status=archived'))
          return fakeResponse({ body: { data: [], next_cursor: null } });
        return fakeResponse({ body: { data: [makeSession('sess-1')], next_cursor: null } });
      }
      return null;
    });
    renderChatPage();
    await screen.findByTestId('chat-session-sess-1');
    // agents catch 置空 → 仅剩占位「All agents」一项
    expect(within(screen.getByTestId('chat-filter-agent')).getAllByRole('option')).toHaveLength(1);
  });

  it('取消归档:PATCH status=active,会话移回活跃区(§6.14)', async () => {
    const recorder: Recorder = { calls: [] };
    stubApi((url, method) => {
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (url.includes('/agents'))
        return fakeResponse({ body: { data: [agent], next_cursor: null } });
      if (url.includes('/favorites') && method === 'GET')
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/chat-sessions/sess-arc') && method === 'PATCH')
        return fakeResponse({
          body: {
            data: makeSession('sess-arc', {
              status: 'active',
              updated_at: '2026-07-02T00:00:00Z',
            }),
          },
        });
      if (url.includes('/chat-sessions') && method === 'GET') {
        if (url.includes('status=archived'))
          return fakeResponse({
            body: { data: [makeSession('sess-arc', { status: 'archived' })], next_cursor: null },
          });
        return fakeResponse({ body: { data: [makeSession('sess-1')], next_cursor: null } });
      }
      return null;
    }, recorder);
    renderChatPage();
    await screen.findByTestId('chat-session-sess-arc');
    fireEvent.click(screen.getByTestId('chat-session-archive-sess-arc'));
    await waitFor(() =>
      expect(
        recorder.calls.some(
          (c) => c.method === 'PATCH' && c.url.includes('/chat-sessions/sess-arc'),
        ),
      ).toBe(true),
    );
    // 会话移回活跃区:归档分组消失,行仍在列表
    await waitFor(() => expect(screen.queryByTestId('chat-sessions-archived')).toBeNull());
    expect(screen.getByTestId('chat-session-sess-arc')).toBeInTheDocument();
    expect(screen.getByTestId('chat-session-sess-1')).toBeInTheDocument();
  });

  it('删除失败:toast 提示且会话保留', async () => {
    const recorder: Recorder = { calls: [] };
    stubApi((url, method) => {
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (url.includes('/agents'))
        return fakeResponse({ body: { data: [agent], next_cursor: null } });
      if (url.includes('/favorites') && method === 'GET')
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/chat-sessions/sess-1') && method === 'DELETE')
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        });
      if (url.includes('/chat-sessions') && method === 'GET') {
        if (url.includes('status=archived'))
          return fakeResponse({ body: { data: [], next_cursor: null } });
        return fakeResponse({ body: { data: [makeSession('sess-1')], next_cursor: null } });
      }
      return null;
    }, recorder);
    renderChatPage();
    await screen.findByTestId('chat-session-sess-1');
    fireEvent.click(screen.getByTestId('chat-session-delete-sess-1'));
    fireEvent.click(screen.getByTestId('chat-session-delete-confirm'));
    await waitFor(() => expect(document.querySelector('.mesh-toast')).not.toBeNull());
    expect(screen.getByTestId('chat-session-sess-1')).toBeInTheDocument();
  });

  it('快捷键 chat.send 的 run 聚焦输入框(命令登记可执行)', async () => {
    stubApi((url, method) => {
      if (url.includes('/users/me')) return fakeResponse({ body: { data: me } });
      if (url.includes('/agents'))
        return fakeResponse({ body: { data: [agent], next_cursor: null } });
      if (url.includes('/favorites') && method === 'GET')
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/chat-sessions/sess-1/messages'))
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/chat-sessions') && method === 'GET') {
        if (url.includes('status=archived'))
          return fakeResponse({ body: { data: [], next_cursor: null } });
        return fakeResponse({ body: { data: [makeSession('sess-1')], next_cursor: null } });
      }
      return null;
    });
    renderChatPage('/chat/sess-1');
    await screen.findByTestId('chat-conversation');
    const sendShortcut = useShortcutRegistry
      .getState()
      .shortcuts.find((def) => def.id === 'chat.send');
    expect(sendShortcut).toBeDefined();
    act(() => {
      sendShortcut?.run();
    });
    expect(document.activeElement).toBe(screen.getByTestId('chat-composer-input'));
  });
});
