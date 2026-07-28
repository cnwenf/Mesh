/**
 * ContextBar 测试(chat-session.md §4.2):关联 issue/project 呈现与各自移除、
 * 二者皆无的弱化提示、添加入口打开 ContextPicker 设定/清除 issue 上下文、
 * issue 解析失败回退 id、patch 失败 toast(404/403 可见性亦走此路径)。
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { ContextBar } from '../ContextBar';
import type { ChatSession } from '../types';

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

interface Recorder {
  calls: { url: string; method: string; body: string }[];
}

function routedClient(
  router: (url: string, method: string) => Response | null,
  recorder: Recorder,
): MeshApiClient {
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    recorder.calls.push({ url, method, body: String(init?.body ?? '') });
    const response = router(url, method);
    if (response !== null) return response;
    return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'x' } } });
  }) as typeof fetch;
  return new MeshApiClient({ baseUrl: 'http://api', getToken: () => null, fetchImpl });
}

const issueDetail = { id: 'iss-1', identifier: 'WEB-1', title: 'Bug' };
const projectDetail = { id: 'prj-1', name: 'Atlas', key: 'ATL' };

/** 标准路由:issue/project 详情可解析,PATCH 回写成功。 */
function standardRouter(patchResponse?: Response) {
  return (url: string, method: string): Response | null => {
    if (url.includes('/api/v1/issues/iss-1') && method === 'GET')
      return fakeResponse({ body: { data: issueDetail } });
    if (url.includes('/api/v1/projects/prj-1') && method === 'GET')
      return fakeResponse({ body: { data: projectDetail } });
    if (url.includes('/chat-sessions/sess-1') && method === 'PATCH')
      return patchResponse ?? fakeResponse({ body: { data: makeSession() } });
    if (url.includes('/issues') && method === 'GET')
      return fakeResponse({ body: { data: [issueDetail], next_cursor: null } });
    return null;
  };
}

function renderBar(session: ChatSession, client: MeshApiClient, onSessionUpdated = vi.fn()) {
  return renderWithProviders(
    <ContextBar
      client={client}
      workspaceId="ws-1"
      session={session}
      onSessionUpdated={onSessionUpdated}
    />,
  );
}

describe('ContextBar(§4.2)', () => {
  it('呈现关联 issue,移除 → PATCH context_issue_id:null + 回写', async () => {
    const recorder: Recorder = { calls: [] };
    const client = routedClient(standardRouter(), recorder);
    const onSessionUpdated = vi.fn();
    renderBar(makeSession({ context_issue_id: 'iss-1' }), client, onSessionUpdated);
    await screen.findByTestId('chat-context-bar');
    // issue 详情异步解析后展示 identifier(兜底 waitFor 解析时机)
    await waitFor(() =>
      expect(screen.getByTestId('chat-context-bar').textContent).toContain('WEB-1'),
    );
    fireEvent.click(screen.getByTestId('chat-context-remove'));
    await waitFor(() => expect(onSessionUpdated).toHaveBeenCalledTimes(1));
    const patch = recorder.calls.find((c) => c.method === 'PATCH');
    expect(JSON.parse(patch?.body ?? '{}')).toEqual({ context_issue_id: null });
  });

  it('呈现关联 project,移除 → PATCH context_project_id:null', async () => {
    const recorder: Recorder = { calls: [] };
    const client = routedClient(standardRouter(), recorder);
    const onSessionUpdated = vi.fn();
    renderBar(makeSession({ context_project_id: 'prj-1' }), client, onSessionUpdated);
    await screen.findByTestId('chat-context-project');
    // project 详情异步解析后展示名称(兜底 waitFor 解析时机)
    await waitFor(() =>
      expect(screen.getByTestId('chat-context-project').textContent).toContain('Atlas'),
    );
    fireEvent.click(screen.getByTestId('chat-context-project-remove'));
    await waitFor(() => expect(onSessionUpdated).toHaveBeenCalledTimes(1));
    const patch = recorder.calls.find((c) => c.method === 'PATCH');
    expect(JSON.parse(patch?.body ?? '{}')).toEqual({ context_project_id: null });
  });

  it('二者皆无时呈现弱化提示 + 添加入口', async () => {
    const recorder: Recorder = { calls: [] };
    const client = routedClient(standardRouter(), recorder);
    renderBar(makeSession(), client);
    expect(await screen.findByTestId('chat-context-prompt')).toBeInTheDocument();
    expect(screen.getByTestId('chat-context-add')).toBeInTheDocument();
  });

  it('添加入口 → picker 选 issue → PATCH context_issue_id + 回写', async () => {
    const user = userEvent.setup();
    const recorder: Recorder = { calls: [] };
    const client = routedClient(standardRouter(), recorder);
    const onSessionUpdated = vi.fn();
    renderBar(makeSession(), client, onSessionUpdated);
    fireEvent.click(await screen.findByTestId('chat-context-add'));
    await user.type(screen.getByTestId('chat-context-picker-search'), 'Bug');
    await user.click(await screen.findByTestId('chat-context-picker-option-iss-1'));
    await waitFor(() => expect(onSessionUpdated).toHaveBeenCalledTimes(1));
    const patch = recorder.calls.find((c) => c.method === 'PATCH');
    expect(JSON.parse(patch?.body ?? '{}')).toEqual({ context_issue_id: 'iss-1' });
  });

  it('picker 清除 → PATCH context_issue_id:null', async () => {
    const user = userEvent.setup();
    const recorder: Recorder = { calls: [] };
    const client = routedClient(standardRouter(), recorder);
    const onSessionUpdated = vi.fn();
    renderBar(makeSession({ context_issue_id: 'iss-1' }), client, onSessionUpdated);
    fireEvent.click(await screen.findByTestId('chat-context-add'));
    await user.click(await screen.findByTestId('chat-context-picker-clear'));
    await waitFor(() => expect(onSessionUpdated).toHaveBeenCalledTimes(1));
    const patch = recorder.calls.find((c) => c.method === 'PATCH');
    expect(JSON.parse(patch?.body ?? '{}')).toEqual({ context_issue_id: null });
  });

  it('issue 解析失败回退展示 id', async () => {
    const recorder: Recorder = { calls: [] };
    const client = routedClient((url, method) => {
      if (url.includes('/api/v1/issues/iss-1') && method === 'GET')
        return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'x' } } });
      return standardRouter()(url, method);
    }, recorder);
    renderBar(makeSession({ context_issue_id: 'iss-1' }), client);
    const bar = await screen.findByTestId('chat-context-bar');
    expect(bar.textContent).toContain('iss-1');
  });

  it('project 解析失败回退展示 id', async () => {
    const recorder: Recorder = { calls: [] };
    const client = routedClient((url, method) => {
      if (url.includes('/api/v1/projects/prj-1') && method === 'GET')
        return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'x' } } });
      return standardRouter()(url, method);
    }, recorder);
    renderBar(makeSession({ context_project_id: 'prj-1' }), client);
    const project = await screen.findByTestId('chat-context-project');
    expect(project.textContent).toContain('prj-1');
  });

  it('PATCH 失败(403 可见性)→ toast,不回写', async () => {
    const recorder: Recorder = { calls: [] };
    const client = routedClient(
      standardRouter(
        fakeResponse({ status: 403, body: { error: { code: 'forbidden', message: 'x' } } }),
      ),
      recorder,
    );
    const onSessionUpdated = vi.fn();
    renderBar(makeSession({ context_issue_id: 'iss-1' }), client, onSessionUpdated);
    await screen.findByTestId('chat-context-bar');
    fireEvent.click(screen.getByTestId('chat-context-remove'));
    await waitFor(() => expect(document.querySelector('.mesh-toast')).not.toBeNull());
    expect(onSessionUpdated).not.toHaveBeenCalled();
  });

  it('picker 取消(onClose)关闭对话框', async () => {
    const user = userEvent.setup();
    const recorder: Recorder = { calls: [] };
    const client = routedClient(standardRouter(), recorder);
    renderBar(makeSession(), client);
    fireEvent.click(await screen.findByTestId('chat-context-add'));
    expect(await screen.findByTestId('chat-context-picker')).toBeInTheDocument();
    await user.click(screen.getByTestId('chat-context-picker-cancel'));
    await waitFor(() => expect(screen.queryByTestId('chat-context-picker')).toBeNull());
  });
});
