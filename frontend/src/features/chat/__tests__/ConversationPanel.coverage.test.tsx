/**
 * ConversationPanel 分支覆盖测试(chat-session.md §4.2):加载更旧、上下文条渲染/移除(成功/失败)、
 * 重生成(成功/失败)、流式中中断(stop)、沉淀入口(禁用/启用/打开)、引用、归档禁用、流内 error toast。
 */
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { RealtimeContext } from '../../../shell/AppShell';
import type { RealtimeContextValue } from '../../../shell/AppShell';
import { renderWithProviders } from '../../../test-utils/render';
import { CHAT_EDIT_LAST_EVENT, ConversationPanel } from '../ConversationPanel';
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

interface Recorder {
  calls: { url: string; method: string }[];
}

function routedClient(
  router: (url: string, method: string) => Response | null,
  recorder?: Recorder,
): MeshApiClient {
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    recorder?.calls.push({ url, method });
    const response = router(url, method);
    if (response !== null) return response;
    return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'x' } } });
  }) as typeof fetch;
  return new MeshApiClient({ baseUrl: 'http://api', getToken: () => null, fetchImpl });
}

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

/** 永不关闭的流(保持 streaming,供 stop 测试)。 */
function sseOpenFetch(frames: readonly string[]): typeof fetch {
  return (async () => ({
    ok: true,
    status: 200,
    body: new ReadableStream<Uint8Array>({
      start(controller) {
        for (const frame of frames) controller.enqueue(encoder.encode(frame));
      },
    }),
    headers: { get: () => null },
  })) as unknown as typeof fetch;
}

afterEach(() => vi.unstubAllGlobals());

describe('ConversationPanel 分支覆盖(§4.2)', () => {
  it('加载更旧消息(游标分页,前置)', async () => {
    const newer = makeMessage({ id: 'm-new', content: 'newer' });
    const older = makeMessage({ id: 'm-old', content: 'older' });
    const client = routedClient((url) => {
      if (!url.includes('/messages')) return null;
      if (url.includes('cursor=cur1'))
        return fakeResponse({ body: { data: [older], next_cursor: null } });
      return fakeResponse({ body: { data: [newer], next_cursor: 'cur1' } });
    });
    renderWithProviders(
      <ConversationPanel
        client={client}
        workspaceId="ws-1"
        session={makeSession()}
        locale="en"
        onSessionUpdated={vi.fn()}
      />,
    );
    await screen.findByTestId('chat-message-m-new');
    fireEvent.click(screen.getByTestId('chat-load-older'));
    expect(await screen.findByTestId('chat-message-m-old')).toBeInTheDocument();
  });

  it('加载更旧失败 toast 但不崩溃', async () => {
    const newer = makeMessage({ id: 'm-new', content: 'newer' });
    const client = routedClient((url) => {
      if (!url.includes('/messages')) return null;
      if (url.includes('cursor=cur1'))
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        });
      return fakeResponse({ body: { data: [newer], next_cursor: 'cur1' } });
    });
    renderWithProviders(
      <ConversationPanel
        client={client}
        workspaceId="ws-1"
        session={makeSession()}
        locale="en"
        onSessionUpdated={vi.fn()}
      />,
    );
    await screen.findByTestId('chat-message-m-new');
    fireEvent.click(screen.getByTestId('chat-load-older'));
    await waitFor(() => expect(document.querySelector('.mesh-toast')).not.toBeNull());
  });

  it('上下文条渲染关联 issue,移除成功回写父级', async () => {
    const recorder: Recorder = { calls: [] };
    const client = routedClient((url, method) => {
      if (url.includes('/messages')) return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/api/v1/issues/iss-1') && method === 'GET')
        return fakeResponse({ body: { data: { id: 'iss-1', identifier: 'WEB-1', title: 'Bug' } } });
      if (url.includes('/chat-sessions/sess-1') && method === 'PATCH')
        return fakeResponse({ body: { data: makeSession({ context_issue_id: null }) } });
      return null;
    }, recorder);
    const onSessionUpdated = vi.fn();
    renderWithProviders(
      <ConversationPanel
        client={client}
        workspaceId="ws-1"
        session={makeSession({ context_issue_id: 'iss-1' })}
        locale="en"
        onSessionUpdated={onSessionUpdated}
      />,
    );
    const bar = await screen.findByTestId('chat-context-bar');
    // identifier 经异步解析(先回退 id,解析成功后替换为 WEB-1)。
    await waitFor(() => expect(bar.textContent).toContain('WEB-1'));
    fireEvent.click(screen.getByTestId('chat-context-remove'));
    await waitFor(() => expect(onSessionUpdated).toHaveBeenCalledTimes(1));
    expect(recorder.calls.some((c) => c.method === 'PATCH')).toBe(true);
  });

  it('移除上下文失败 toast', async () => {
    const client = routedClient((url, method) => {
      if (url.includes('/messages')) return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/api/v1/issues/iss-1') && method === 'GET')
        return fakeResponse({ body: { data: { id: 'iss-1', identifier: 'WEB-1', title: 'Bug' } } });
      if (method === 'PATCH')
        return fakeResponse({ status: 409, body: { error: { code: 'conflict', message: 'x' } } });
      return null;
    });
    const onSessionUpdated = vi.fn();
    renderWithProviders(
      <ConversationPanel
        client={client}
        workspaceId="ws-1"
        session={makeSession({ context_issue_id: 'iss-1' })}
        locale="en"
        onSessionUpdated={onSessionUpdated}
      />,
    );
    await screen.findByTestId('chat-context-bar');
    fireEvent.click(screen.getByTestId('chat-context-remove'));
    await waitFor(() => expect(document.querySelector('.mesh-toast')).not.toBeNull());
    expect(onSessionUpdated).not.toHaveBeenCalled();
  });

  it('上下文 issue 解析失败回退显示 id', async () => {
    const client = routedClient((url, method) => {
      if (url.includes('/messages')) return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/api/v1/issues/iss-1') && method === 'GET')
        return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'x' } } });
      return null;
    });
    renderWithProviders(
      <ConversationPanel
        client={client}
        workspaceId="ws-1"
        session={makeSession({ context_issue_id: 'iss-1' })}
        locale="en"
        onSessionUpdated={vi.fn()}
      />,
    );
    const bar = await screen.findByTestId('chat-context-bar');
    expect(bar.textContent).toContain('iss-1');
  });

  it('重生成:POST regenerate + 新候选流式落库', async () => {
    const recorder: Recorder = { calls: [] };
    const agentMsg = makeMessage({ id: 'm-a', role: 'agent', content: 'old answer' });
    let messageCalls = 0;
    const client = routedClient((url, method) => {
      if (url.includes('/messages') && method === 'GET') {
        messageCalls += 1;
        if (messageCalls === 1)
          return fakeResponse({ body: { data: [agentMsg], next_cursor: null } });
        return fakeResponse({
          body: {
            data: [
              makeMessage({ id: 'm-new', role: 'agent', content: 'new answer', parent_id: 'u-1' }),
              agentMsg,
            ],
            next_cursor: null,
          },
        });
      }
      if (url.includes('/regenerate'))
        return fakeResponse({
          body: {
            data: { message_id: 'm-new', generation_id: 'g2', stream_url: 'http://s/stream' },
          },
        });
      return null;
    }, recorder);
    vi.stubGlobal(
      'fetch',
      sseFetch([
        'id: 1\nevent: message.created\ndata: {"message_id":"m-new","role":"agent","generation_status":"streaming"}\n\n',
        'id: 2\nevent: message.delta\ndata: {"message_id":"m-new","delta":"new answer"}\n\n',
        'id: 3\nevent: message.done\ndata: {"message_id":"m-new","generation_status":"done"}\n\n',
      ]),
    );
    renderWithProviders(
      <ConversationPanel
        client={client}
        workspaceId="ws-1"
        session={makeSession()}
        locale="en"
        onSessionUpdated={vi.fn()}
      />,
    );
    await screen.findByTestId('chat-message-m-a');
    fireEvent.click(screen.getByTestId('chat-regenerate-m-a'));
    await waitFor(() =>
      expect(recorder.calls.some((c) => c.url.includes('/regenerate'))).toBe(true),
    );
    expect(await screen.findByTestId('chat-message-m-new')).toBeInTheDocument();
  });

  it('重生成失败 toast', async () => {
    const recorder: Recorder = { calls: [] };
    const agentMsg = makeMessage({ id: 'm-a', role: 'agent', content: 'answer' });
    const client = routedClient((url, method) => {
      if (url.includes('/messages') && method === 'GET')
        return fakeResponse({ body: { data: [agentMsg], next_cursor: null } });
      if (url.includes('/regenerate'))
        return fakeResponse({
          status: 429,
          body: { error: { code: 'rate_limited', message: 'x' } },
        });
      return null;
    }, recorder);
    renderWithProviders(
      <ConversationPanel
        client={client}
        workspaceId="ws-1"
        session={makeSession()}
        locale="en"
        onSessionUpdated={vi.fn()}
      />,
    );
    await screen.findByTestId('chat-message-m-a');
    fireEvent.click(screen.getByTestId('chat-regenerate-m-a'));
    await waitFor(() => expect(document.querySelector('.mesh-toast')).not.toBeNull());
  });

  it('流式进行中呈现 Stop,点击中断经 stop 端点', async () => {
    const recorder: Recorder = { calls: [] };
    const seed = makeMessage({ id: 'u-seed', role: 'user', content: 'seed' });
    const client = routedClient((url, method) => {
      if (url.includes('/messages') && method === 'GET')
        return fakeResponse({ body: { data: [seed], next_cursor: null } });
      if (url.includes('/messages') && method === 'POST')
        return fakeResponse({
          body: {
            data: { message_id: 'u-new', generation_id: 'gen-9', stream_url: 'http://s/stream' },
          },
        });
      if (url.includes('/stop'))
        return fakeResponse({
          body: {
            data: { generation_id: 'gen-9', message_id: 'm-a', generation_status: 'interrupted' },
          },
        });
      return null;
    }, recorder);
    vi.stubGlobal(
      'fetch',
      sseOpenFetch([
        'id: 1\nevent: message.created\ndata: {"message_id":"m-a","role":"agent","generation_status":"streaming"}\n\n',
      ]),
    );
    renderWithProviders(
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
    const stopBtn = await screen.findByTestId('chat-composer-stop');
    fireEvent.click(stopBtn);
    await waitFor(() =>
      expect(recorder.calls.some((c) => c.url.includes('/generations/gen-9/stop'))).toBe(true),
    );
    await waitFor(() => expect(screen.queryByTestId('chat-composer-stop')).toBeNull());
  });

  it('流式排队期:首帧未达呈现占位打字气泡 + 已耗时(§4.2/§9.8)', async () => {
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
    // 保持流打开且无任何帧 → liveMessage 仍为 null,占位气泡承接排队窗口。
    vi.stubGlobal('fetch', sseOpenFetch([]));
    renderWithProviders(
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
    expect(
      await screen.findByTestId('chat-typing-local-streaming-placeholder'),
    ).toBeInTheDocument();
    // 头部运行反馈:运行态徽标 + 已耗时秒数。
    expect(screen.getByTestId('chat-run-elapsed')).toBeInTheDocument();
    // 排队期 composer 以停止按钮替换发送按钮。
    expect(screen.getByTestId('chat-composer-stop')).toBeInTheDocument();
  });

  it('沉淀入口:无上下文时禁用', async () => {
    const client = routedClient((url) =>
      url.includes('/messages') ? fakeResponse({ body: { data: [], next_cursor: null } }) : null,
    );
    renderWithProviders(
      <ConversationPanel
        client={client}
        workspaceId="ws-1"
        session={makeSession()}
        locale="en"
        onSessionUpdated={vi.fn()}
      />,
    );
    await screen.findByTestId('chat-conversation');
    expect(screen.getByTestId('chat-distill-open')).toBeDisabled();
  });

  it('沉淀入口:有上下文启用并可打开对话框', async () => {
    const client = routedClient((url) => {
      if (url.includes('/messages')) return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/distill-preview'))
        return fakeResponse({
          body: {
            data: {
              target_issue: { id: 'iss-1', identifier: 'WEB-1', title: 'Bug' },
              body_markdown: '# body',
              attachments: [],
              triggered_agents: [],
              mentions: [],
              can_trigger_agents: false,
              suppress_triggers_supported: false,
            },
          },
        });
      return null;
    });
    renderWithProviders(
      <ConversationPanel
        client={client}
        workspaceId="ws-1"
        session={makeSession({ context_issue_id: 'iss-1' })}
        locale="en"
        onSessionUpdated={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByTestId('chat-distill-open')).toBeEnabled());
    fireEvent.click(screen.getByTestId('chat-distill-open'));
    expect(await screen.findByTestId('chat-distill')).toBeInTheDocument();
  });

  it('引用消息:点击 quote 在 composer 呈现引用横幅', async () => {
    const agentMsg = makeMessage({ id: 'm-a', role: 'agent', content: 'quotable' });
    const client = routedClient((url) =>
      url.includes('/messages')
        ? fakeResponse({ body: { data: [agentMsg], next_cursor: null } })
        : null,
    );
    renderWithProviders(
      <ConversationPanel
        client={client}
        workspaceId="ws-1"
        session={makeSession()}
        locale="en"
        onSessionUpdated={vi.fn()}
      />,
    );
    await screen.findByTestId('chat-message-m-a');
    fireEvent.click(screen.getByTestId('chat-quote-action-m-a'));
    expect(await screen.findByTestId('chat-composer-quote')).toHaveTextContent('quotable');
    fireEvent.click(screen.getByTestId('chat-composer-clear-quote'));
    await waitFor(() => expect(screen.queryByTestId('chat-composer-quote')).toBeNull());
  });

  it('归档会话禁用输入', async () => {
    const client = routedClient((url) =>
      url.includes('/messages') ? fakeResponse({ body: { data: [], next_cursor: null } }) : null,
    );
    renderWithProviders(
      <ConversationPanel
        client={client}
        workspaceId="ws-1"
        session={makeSession({ status: 'archived' })}
        locale="en"
        onSessionUpdated={vi.fn()}
      />,
    );
    await screen.findByTestId('chat-conversation');
    expect(screen.getByTestId('chat-composer-input')).toBeDisabled();
  });

  it('流内 error 事件 toast + 失败气泡', async () => {
    const failedAgent = makeMessage({
      id: 'm-a',
      role: 'agent',
      generation_status: 'failed',
      error_message: 'slow',
      content: '',
    });
    let messageCalls = 0;
    const client = routedClient((url, method) => {
      if (url.includes('/messages') && method === 'GET') {
        messageCalls += 1;
        if (messageCalls === 1)
          return fakeResponse({
            body: {
              data: [makeMessage({ id: 'u-seed', role: 'user', content: 'seed' })],
              next_cursor: null,
            },
          });
        return fakeResponse({ body: { data: [failedAgent], next_cursor: null } });
      }
      if (url.includes('/messages') && method === 'POST')
        return fakeResponse({
          body: {
            data: { message_id: 'u-new', generation_id: 'gen-1', stream_url: 'http://s/stream' },
          },
        });
      return null;
    });
    vi.stubGlobal(
      'fetch',
      sseFetch([
        'id: 1\nevent: message.created\ndata: {"message_id":"m-a","role":"agent","generation_status":"streaming"}\n\n',
        'id: 2\nevent: error\ndata: {"message_id":"m-a","code":"rate_limited","message":"slow"}\n\n',
      ]),
    );
    renderWithProviders(
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
    fireEvent.change(input, { target: { value: 'trigger error' } });
    fireEvent.keyDown(input, { key: 'Enter', metaKey: true });
    // 流内 error → toast
    await waitFor(() => expect(document.querySelector('.mesh-toast')).not.toBeNull());
    // 重拉后失败气泡呈现错误文案
    expect(await screen.findByTestId('chat-error-m-a')).toHaveTextContent('slow');
  });

  it('agent 消息含多候选时渲染候选切换器', async () => {
    const agentMsg = makeMessage({
      id: 'm-a',
      role: 'agent',
      content: 'answer',
      parent_id: 'u-1',
      candidate_count: 2,
      candidate_index: 0,
    });
    const client = routedClient((url) =>
      url.includes('/messages')
        ? fakeResponse({ body: { data: [agentMsg], next_cursor: null } })
        : null,
    );
    renderWithProviders(
      <ConversationPanel
        client={client}
        workspaceId="ws-1"
        session={makeSession()}
        locale="en"
        onSessionUpdated={vi.fn()}
      />,
    );
    await screen.findByTestId('chat-message-m-a');
    expect(screen.getByTestId('chat-candidates-m-a')).toBeInTheDocument();
  });

  it('关闭沉淀对话框(onClose 回写)', async () => {
    const client = routedClient((url) => {
      if (url.includes('/messages')) return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/distill-preview'))
        return fakeResponse({
          body: {
            data: {
              target_issue: { id: 'iss-1', identifier: 'WEB-1', title: 'Bug' },
              body_markdown: '# body',
              attachments: [],
              triggered_agents: [],
              mentions: [],
              can_trigger_agents: false,
              suppress_triggers_supported: false,
            },
          },
        });
      return null;
    });
    renderWithProviders(
      <ConversationPanel
        client={client}
        workspaceId="ws-1"
        session={makeSession({ context_issue_id: 'iss-1' })}
        locale="en"
        onSessionUpdated={vi.fn()}
      />,
    );
    fireEvent.click(await screen.findByTestId('chat-distill-open'));
    expect(await screen.findByTestId('chat-distill')).toBeInTheDocument();
    // Esc 关闭(Dialog → onClose)
    fireEvent.keyDown(screen.getByTestId('chat-distill'), { key: 'Escape' });
    await waitFor(() => expect(screen.queryByTestId('chat-distill')).toBeNull());
  });

  it('沉淀提交成功 → createComment + onDistilled 关闭对话框', async () => {
    const recorder: Recorder = { calls: [] };
    const client = routedClient((url, method) => {
      if (url.includes('/messages')) return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/distill-preview'))
        return fakeResponse({
          body: {
            data: {
              target_issue: { id: 'iss-1', identifier: 'WEB-1', title: 'Bug' },
              body_markdown: '# body',
              attachments: [],
              triggered_agents: [],
              mentions: [],
              can_trigger_agents: false,
              suppress_triggers_supported: false,
            },
          },
        });
      if (url.includes('/issues/iss-1/comments') && method === 'POST')
        return fakeResponse({ status: 201, body: { data: { id: 'c-1' } } });
      return null;
    }, recorder);
    renderWithProviders(
      <ConversationPanel
        client={client}
        workspaceId="ws-1"
        session={makeSession({ context_issue_id: 'iss-1' })}
        locale="en"
        onSessionUpdated={vi.fn()}
      />,
    );
    fireEvent.click(await screen.findByTestId('chat-distill-open'));
    await screen.findByTestId('chat-distill');
    await waitFor(() => expect(screen.getByTestId('chat-distill-submit')).toBeEnabled());
    fireEvent.click(screen.getByTestId('chat-distill-submit'));
    await waitFor(() =>
      expect(
        recorder.calls.some((c) => c.method === 'POST' && c.url.includes('/issues/iss-1/comments')),
      ).toBe(true),
    );
    // onDistilled → 关闭对话框
    await waitFor(() => expect(screen.queryByTestId('chat-distill')).toBeNull());
  });

  it('WS 会话频道帧(非流式终态)→ 重拉对账;频道/事件不匹配忽略;卸载清理', async () => {
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
    let messageCalls = 0;
    const client = routedClient((url, method) => {
      if (url.includes('/messages') && method === 'GET') {
        messageCalls += 1;
        if (messageCalls === 1)
          return fakeResponse({ body: { data: [makeMessage({ id: 'm-1' })], next_cursor: null } });
        return fakeResponse({
          body: {
            data: [makeMessage({ id: 'm-2' }), makeMessage({ id: 'm-1' })],
            next_cursor: null,
          },
        });
      }
      return null;
    });
    const { unmount } = renderWithProviders(
      <RealtimeContext.Provider value={fakeRealtime}>
        <ConversationPanel
          client={client}
          workspaceId="ws-1"
          session={makeSession()}
          locale="en"
          onSessionUpdated={vi.fn()}
        />
      </RealtimeContext.Provider>,
    );
    await screen.findByTestId('chat-message-m-1');
    expect(fakeRealtime.client.subscribe).toHaveBeenCalledWith('chat_session:sess-1');
    // 频道不匹配 → 忽略;非终态事件 → 忽略(均不重拉)
    for (const cb of listeners)
      cb({ op: 'event', channel: 'other:ch', seq: 1, event: 'message.done', payload: {} });
    for (const cb of listeners)
      cb({
        op: 'event',
        channel: 'chat_session:sess-1',
        seq: 2,
        event: 'message.delta',
        payload: {},
      });
    expect(messageCalls).toBe(1);
    // 终态事件 message.created → 重拉对账
    for (const cb of listeners)
      cb({
        op: 'event',
        channel: 'chat_session:sess-1',
        seq: 3,
        event: 'message.created',
        payload: {},
      });
    await waitFor(() => expect(messageCalls).toBe(2));
    expect(await screen.findByTestId('chat-message-m-2')).toBeInTheDocument();
    // 卸载触发 unsubscribe(清理路径)
    unmount();
    expect(fakeRealtime.client.unsubscribe).toHaveBeenCalledWith('chat_session:sess-1');
  });

  it('候选「使用此条」→ select 落库 + handleSelectCandidate 替换展示', async () => {
    const recorder: Recorder = { calls: [] };
    const selected = makeMessage({
      id: 'm-a',
      role: 'agent',
      content: 'answer A',
      parent_id: 'u-1',
      candidate_count: 2,
      candidate_index: 0,
    });
    const sibling = makeMessage({
      id: 'm-b',
      role: 'agent',
      content: 'answer B',
      parent_id: 'u-1',
      candidate_count: 2,
      candidate_index: 1,
    });
    let _messageCalls = 0;
    const client = routedClient((url, method) => {
      if (url.includes('/messages') && method === 'GET') {
        _messageCalls += 1;
        // 首次:会话消息(选中候选 m-a);候选列表查询(parent_id)返回两兄弟
        if (url.includes('parent_id=u-1'))
          return fakeResponse({ body: { data: [selected, sibling], next_cursor: null } });
        return fakeResponse({ body: { data: [selected], next_cursor: null } });
      }
      if (url.includes('/messages/u-1/select') && method === 'POST')
        return fakeResponse({ body: { data: { parent_id: 'u-1', selected_message_id: 'm-b' } } });
      return null;
    }, recorder);
    renderWithProviders(
      <ConversationPanel
        client={client}
        workspaceId="ws-1"
        session={makeSession()}
        locale="en"
        onSessionUpdated={vi.fn()}
      />,
    );
    await screen.findByTestId('chat-candidates-m-a');
    // 翻到兄弟候选 m-b(本地),再「使用此条」落库
    fireEvent.click(screen.getByTestId('chat-candidate-next-m-a'));
    await waitFor(() => expect(screen.getByTestId('chat-candidate-use-m-a')).toBeEnabled());
    fireEvent.click(screen.getByTestId('chat-candidate-use-m-a'));
    await waitFor(() =>
      expect(
        recorder.calls.some((c) => c.method === 'POST' && c.url.includes('/messages/u-1/select')),
      ).toBe(true),
    );
  });

  it('mod+↑ 编辑上一条自己消息:草稿种子预填 composer(§4.3 S12)', async () => {
    const userMsg = makeMessage({ id: 'u-1', role: 'user', content: 'edit me please' });
    const agentMsg = makeMessage({ id: 'm-2', role: 'agent', content: 'ok' });
    const client = routedClient((url, method) => {
      if (url.includes('/messages') && method === 'GET')
        return fakeResponse({ body: { data: [userMsg, agentMsg], next_cursor: null } });
      return null;
    });
    renderWithProviders(
      <ConversationPanel
        client={client}
        workspaceId="ws-1"
        session={makeSession()}
        locale="en"
        onSessionUpdated={vi.fn()}
      />,
    );
    await screen.findByTestId('chat-message-m-2');
    // 派发编辑上一条事件(与 chat.edit.last 快捷键同一事件通道)
    act(() => {
      window.dispatchEvent(new CustomEvent(CHAT_EDIT_LAST_EVENT));
    });
    expect((screen.getByTestId('chat-composer-input') as HTMLTextAreaElement).value).toBe(
      'edit me please',
    );
  });

  it('滚动守卫:scroll 事件更新 nearBottom 判定(不打扰回看)', async () => {
    const msg = makeMessage({ id: 'm-1', content: 'hello' });
    const client = routedClient((url, method) => {
      if (url.includes('/messages') && method === 'GET')
        return fakeResponse({ body: { data: [msg], next_cursor: null } });
      return null;
    });
    renderWithProviders(
      <ConversationPanel
        client={client}
        workspaceId="ws-1"
        session={makeSession()}
        locale="en"
        onSessionUpdated={vi.fn()}
      />,
    );
    const list = await screen.findByTestId('chat-messages');
    // 模拟用户上翻后触发 scroll 监听(handleScroll 重算 nearBottom)
    fireEvent.scroll(list);
    expect(screen.getByTestId('chat-message-m-1')).toBeInTheDocument();
  });
});
