/**
 * 评论 API 契约层测试(comment-inbox.md §3.1):路径 / 方法 / 包络 / If-Match / 频道名。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api';
import { fakeResponse, headersOf, stubFetch } from '../../../api/__tests__/fetchStub';
import type { FetchStub } from '../../../api/__tests__/fetchStub';
import {
  addReaction,
  createComment,
  deleteComment,
  getComment,
  issueChannel,
  listComments,
  listReactions,
  listReplies,
  removeReaction,
  reopenThread,
  resolveThread,
  updateComment,
} from '../api';

let stub: FetchStub;
let client: MeshApiClient;

beforeEach(() => {
  vi.unstubAllGlobals();
  stub = stubFetch(fakeResponse({ body: { data: [], next_cursor: null } }));
  vi.stubGlobal('fetch', stub.fetchImpl);
  client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
});

it('builds the issue channel name', () => {
  expect(issueChannel('iss-1')).toBe('issue:iss-1');
});

describe('endpoint surface', () => {
  it('lists comments with query params', async () => {
    await listComments(client, 'iss-1', { limit: 10, include: 'replies', order: 'asc', cursor: 'c' });
    const url = stub.calls[0].url;
    expect(url).toContain('/api/v1/issues/iss-1/comments');
    expect(url).toContain('include=replies');
    expect(url).toContain('order=asc');
    expect(url).toContain('cursor=c');
  });

  it('creates a comment (POST) with suppress_triggers', async () => {
    stub = stubFetch(fakeResponse({ status: 201, body: { data: { id: 'c-1' } } }));
    vi.stubGlobal('fetch', stub.fetchImpl);
    await createComment(client, 'iss-1', { body_markdown: 'hi', suppress_triggers: true });
    expect(stub.calls[0].init?.method).toBe('POST');
    expect(JSON.parse(String(stub.calls[0].init?.body))).toEqual({
      body_markdown: 'hi',
      suppress_triggers: true,
    });
  });

  it('gets a single comment', async () => {
    await getComment(client, 'c-1');
    expect(stub.calls[0].url).toBe('http://api/api/v1/comments/c-1');
  });

  it('updates with If-Match optimistic lock', async () => {
    await updateComment(client, 'c-1', 'new', '2026-07-01T00:00:00Z');
    expect(stub.calls[0].init?.method).toBe('PATCH');
    expect(headersOf(stub.calls[0])['If-Match']).toBe('2026-07-01T00:00:00Z');
    expect(JSON.parse(String(stub.calls[0].init?.body))).toEqual({ body_markdown: 'new' });
  });

  it('deletes (DELETE)', async () => {
    await deleteComment(client, 'c-1');
    expect(stub.calls[0].init?.method).toBe('DELETE');
  });

  it('lists replies / reactions', async () => {
    await listReplies(client, 'c-1', { limit: 5 });
    await listReactions(client, 'c-1');
    expect(stub.calls[0].url).toContain('/api/v1/comments/c-1/replies');
    expect(stub.calls[1].url).toBe('http://api/api/v1/comments/c-1/reactions');
  });

  it('resolves / reopens a thread', async () => {
    await resolveThread(client, 'c-1');
    await reopenThread(client, 'c-1');
    expect(stub.calls[0].url).toBe('http://api/api/v1/comments/c-1/resolve');
    expect(stub.calls[1].url).toBe('http://api/api/v1/comments/c-1/reopen');
  });

  it('adds and removes reactions (emoji url-encoded)', async () => {
    stub = stubFetch(fakeResponse({ body: { data: [] } }));
    vi.stubGlobal('fetch', stub.fetchImpl);
    await addReaction(client, 'c-1', '👍');
    expect(stub.calls[0].init?.method).toBe('POST');
    expect(JSON.parse(String(stub.calls[0].init?.body))).toEqual({ emoji: '👍' });
    await removeReaction(client, 'c-1', '👍');
    expect(stub.calls[1].init?.method).toBe('DELETE');
    expect(stub.calls[1].url).toContain('/reactions/');
  });
});
