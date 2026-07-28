/**
 * 聊天 API 契约层测试(chat-session.md §3.1–§3.5):路径 / 方法 / 查询 / 体 / If-Match / 频道名。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api';
import { fakeResponse, headersOf, stubFetch } from '../../../api/__tests__/fetchStub';
import type { FetchStub } from '../../../api/__tests__/fetchStub';
import {
  chatSessionChannel,
  createChatSession,
  deleteChatSession,
  deleteSessionFavorite,
  distillPreview,
  getChatSession,
  listChatMessages,
  listChatSessions,
  listSessionFavorites,
  patchChatSession,
  putSessionFavorite,
  regenerateMessage,
  selectCandidate,
  sendMessage,
  stopGeneration,
  chatListChannel,
} from '../api';

let stub: FetchStub;
let client: MeshApiClient;

beforeEach(() => {
  vi.unstubAllGlobals();
  stub = stubFetch(fakeResponse({ body: { data: [], next_cursor: null } }));
  vi.stubGlobal('fetch', stub.fetchImpl);
  client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
});

describe('频道助手(§3.6)', () => {
  it('构造会话级与列表级频道名', () => {
    expect(chatSessionChannel('sess-1')).toBe('chat_session:sess-1');
    expect(chatListChannel('mem-1')).toBe('chat_list:mem-1');
  });
});

describe('会话 CRUD(§3.1)', () => {
  it('创建会话 POST + body', async () => {
    await createChatSession(client, 'ws-1', { agent_id: 'a-1', context_issue_id: 'iss-1', title: 'T' });
    expect(stub.calls[0].url).toBe('http://api/api/v1/workspaces/ws-1/chat-sessions');
    expect(stub.calls[0].init?.method).toBe('POST');
    expect(JSON.parse(String(stub.calls[0].init?.body))).toEqual({
      agent_id: 'a-1',
      context_issue_id: 'iss-1',
      title: 'T',
    });
  });

  it('列表带过滤与分页查询', async () => {
    await listChatSessions(client, 'ws-1', {
      agent_id: 'a-1',
      status: 'archived',
      limit: 10,
      cursor: 'cur',
    });
    const url = stub.calls[0].url;
    expect(url).toContain('/api/v1/workspaces/ws-1/chat-sessions');
    expect(url).toContain('agent_id=a-1');
    expect(url).toContain('status=archived');
    expect(url).toContain('limit=10');
    expect(url).toContain('cursor=cur');
  });

  it('取单个会话 GET', async () => {
    await getChatSession(client, 'ws-1', 'sess-1');
    expect(stub.calls[0].url).toBe('http://api/api/v1/workspaces/ws-1/chat-sessions/sess-1');
  });

  it('PATCH 携带 If-Match 乐观锁', async () => {
    await patchChatSession(client, 'ws-1', 'sess-1', { title: 'New' }, '2026-07-01T00:00:00Z');
    expect(stub.calls[0].init?.method).toBe('PATCH');
    expect(headersOf(stub.calls[0])['If-Match']).toBe('2026-07-01T00:00:00Z');
  });

  it('删除会话 DELETE', async () => {
    await deleteChatSession(client, 'ws-1', 'sess-1');
    expect(stub.calls[0].init?.method).toBe('DELETE');
  });
});

describe('消息与生成(§3.2 / §3.3)', () => {
  it('消息列表带 parent_id 查询', async () => {
    await listChatMessages(client, 'ws-1', 'sess-1', { limit: 5, cursor: 'c', parent_id: 'm-0' });
    const url = stub.calls[0].url;
    expect(url).toContain('/chat-sessions/sess-1/messages');
    expect(url).toContain('parent_id=m-0');
    expect(url).toContain('limit=5');
  });

  it('发送消息 POST + body(含附件与引用)', async () => {
    await sendMessage(client, 'ws-1', 'sess-1', {
      content: 'hi',
      attachment_ids: ['att-1'],
      quote_message_id: 'm-9',
    });
    expect(stub.calls[0].init?.method).toBe('POST');
    expect(JSON.parse(String(stub.calls[0].init?.body))).toEqual({
      content: 'hi',
      attachment_ids: ['att-1'],
      quote_message_id: 'm-9',
    });
  });

  it('重生成 POST 到 regenerate 子路径', async () => {
    await regenerateMessage(client, 'ws-1', 'sess-1', 'm-1');
    expect(stub.calls[0].url).toContain('/messages/m-1/regenerate');
    expect(stub.calls[0].init?.method).toBe('POST');
  });

  it('选中候选 POST + selected_message_id', async () => {
    await selectCandidate(client, 'ws-1', 'sess-1', 'm-1', 'm-2');
    expect(stub.calls[0].url).toContain('/messages/m-1/select');
    expect(JSON.parse(String(stub.calls[0].init?.body))).toEqual({ selected_message_id: 'm-2' });
  });

  it('中断生成 POST 到 generations stop 子路径', async () => {
    await stopGeneration(client, 'ws-1', 'sess-1', 'gen-1');
    expect(stub.calls[0].url).toContain('/generations/gen-1/stop');
    expect(stub.calls[0].init?.method).toBe('POST');
  });

  it('沉淀预览 POST + body', async () => {
    await distillPreview(client, 'ws-1', 'sess-1', {
      body_markdown: '# hi',
      target_issue_id: 'iss-1',
      attachment_ids: ['att-1'],
    });
    expect(stub.calls[0].url).toContain('/distill-preview');
    expect(JSON.parse(String(stub.calls[0].init?.body))).toEqual({
      body_markdown: '# hi',
      target_issue_id: 'iss-1',
      attachment_ids: ['att-1'],
    });
  });
});

describe('置顶 favorites(§6.19)', () => {
  it('PUT 置顶(幂等)', async () => {
    await putSessionFavorite(client, 'sess-1');
    expect(stub.calls[0].url).toBe('http://api/api/v1/favorites/chat_session/sess-1');
    expect(stub.calls[0].init?.method).toBe('PUT');
  });

  it('DELETE 取消置顶', async () => {
    await deleteSessionFavorite(client, 'sess-1');
    expect(stub.calls[0].init?.method).toBe('DELETE');
  });

  it('列出工作区会话收藏', async () => {
    await listSessionFavorites(client, 'ws-1');
    const url = stub.calls[0].url;
    expect(url).toContain('/api/v1/favorites');
    expect(url).toContain('workspace_id=ws-1');
    expect(url).toContain('target_type=chat_session');
  });
});
