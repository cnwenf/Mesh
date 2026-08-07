/**
 * 收件箱 API 契约层测试(comment-inbox.md §3.2):路径 / 方法 / 查询参数 / 频道名。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api';
import { fakeResponse, stubFetch } from '../../../api/__tests__/fetchStub';
import type { FetchStub } from '../../../api/__tests__/fetchStub';
import {
  archiveNotification,
  archiveRead,
  getPreferences,
  inboxChannel,
  listInbox,
  markRead,
  markUnread,
  muteIssue,
  readAll,
  unreadCount,
  unmuteIssue,
  updatePreferences,
} from '../api';

let stub: FetchStub;
let client: MeshApiClient;

beforeEach(() => {
  vi.unstubAllGlobals();
  stub = stubFetch(fakeResponse({ body: { data: [], next_cursor: null } }));
  vi.stubGlobal('fetch', stub.fetchImpl);
  client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
});

it('builds the inbox channel name', () => {
  expect(inboxChannel('mem-1')).toBe('member:mem-1:inbox');
});

describe('endpoint surface', () => {
  it('lists inbox with workspace_id + filter + grouped', async () => {
    await listInbox(client, { workspaceId: 'ws-1', filter: 'unread', grouped: true, limit: 5 });
    const url = stub.calls[0].url;
    expect(url).toContain('/api/v1/inbox');
    expect(url).toContain('workspace_id=ws-1');
    expect(url).toContain('filter=unread');
    expect(url).toContain('grouped=true');
  });

  it('L202: listInbox passes archived=true for the archived view', async () => {
    await listInbox(client, { workspaceId: 'ws-1', archived: true });
    const url = stub.calls[0].url;
    expect(url).toContain('archived=true');
  });

  it('reads the unread count', async () => {
    stub = stubFetch(fakeResponse({ body: { data: { count: 7 } } }));
    vi.stubGlobal('fetch', stub.fetchImpl);
    const count = await unreadCount(client, 'ws-1');
    expect(count).toBe(7);
    expect(stub.calls[0].url).toContain('/api/v1/inbox/unread-count');
  });

  it('marks all read with optional filter', async () => {
    stub = stubFetch(fakeResponse({ body: { data: { updated: 3 } } }));
    vi.stubGlobal('fetch', stub.fetchImpl);
    const updated = await readAll(client, 'ws-1', 'mentions');
    expect(updated).toBe(3);
    expect(JSON.parse(String(stub.calls[0].init?.body))).toEqual({ filter: 'mentions' });
    // 无筛选时 body 为空对象
    await readAll(client, 'ws-1');
    expect(JSON.parse(String(stub.calls[1].init?.body))).toEqual({});
  });

  it('archives read', async () => {
    stub = stubFetch(fakeResponse({ body: { data: { archived: 2 } } }));
    vi.stubGlobal('fetch', stub.fetchImpl);
    expect(await archiveRead(client, 'ws-1')).toBe(2);
  });

  it('marks read / unread / archive a single notification', async () => {
    await markRead(client, 'ws-1', 'n-1');
    await markUnread(client, 'ws-1', 'n-1');
    await archiveNotification(client, 'ws-1', 'n-1');
    expect(stub.calls[0].url).toContain('/api/v1/inbox/n-1/read');
    expect(stub.calls[1].url).toContain('/api/v1/inbox/n-1/unread');
    expect(stub.calls[2].url).toContain('/api/v1/inbox/n-1/archive');
  });

  it('mutes / unmutes an issue', async () => {
    await muteIssue(client, 'iss-1');
    await unmuteIssue(client, 'iss-1');
    expect(stub.calls[0].url).toBe('http://api/api/v1/issues/iss-1/mute');
    expect(stub.calls[1].url).toBe('http://api/api/v1/issues/iss-1/unmute');
  });

  it('gets and updates preferences', async () => {
    await getPreferences(client, 'ws-1');
    expect(stub.calls[0].url).toContain('/api/v1/notification-preferences');
    stub = stubFetch(fakeResponse({ body: { data: [] } }));
    vi.stubGlobal('fetch', stub.fetchImpl);
    await updatePreferences(client, 'ws-1', [{ event_type: 'assigned', in_app: true, email: 'digest' }]);
    expect(stub.calls[0].init?.method).toBe('PUT');
    expect(JSON.parse(String(stub.calls[0].init?.body))).toEqual({
      preferences: [{ event_type: 'assigned', in_app: true, email: 'digest' }],
    });
  });
});
