/**
 * api/search — 搜索端点参数组装/包络解析/错误归一 + 纯函数(isIdentifierQuery /
 * highlightRangesToSpans code point 映射,含 CJK 与越界钳制)。
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { MeshApiClient } from '../client';
import { MeshApiError } from '../errors';
import {
  deleteFavorite,
  highlightRangesToSpans,
  isIdentifierQuery,
  listPaletteFavorites,
  putFavorite,
  searchWorkspace,
  toggleFavoriteForTarget,
} from '../search';
import type { SearchItem } from '../search';
import { fakeResponse, headersOf, stubFetch } from './fetchStub';

function makeClient(fetchImpl: typeof fetch): MeshApiClient {
  return new MeshApiClient({ baseUrl: 'http://api.test', getToken: () => 'tok', fetchImpl });
}

const ISSUE_ITEM: SearchItem = {
  type: 'issue',
  id: 'i-1',
  title: '登录页崩溃',
  context: {
    identifier: 'WEB-124',
    project: { id: 'p-1', name: '官网' },
    status: { id: 's-1', name: 'In Progress', category: 'in_progress' },
  },
  icon: 'issue',
  url: '/w/acme/issues/by-identifier/WEB-124',
  highlight: { title: { unit: 'codepoint', ranges: [[0, 2]] } },
};

describe('searchWorkspace', () => {
  beforeEach(() => undefined);

  it('组装路径与 q/types/limit/cursor 查询参数并解析列表包络', async () => {
    const { fetchImpl, calls } = stubFetch(
      fakeResponse({ body: { data: [ISSUE_ITEM], next_cursor: 'cur-1' } }),
    );
    const client = makeClient(fetchImpl);
    const page = await searchWorkspace(client, 'ws 1', {
      q: '登录',
      types: ['issue', 'member'],
      limit: 5,
      cursor: 'cur-0',
    });
    expect(calls).toHaveLength(1);
    const url = new URL(calls[0].url);
    expect(url.pathname).toBe('/api/v1/workspaces/ws%201/search');
    expect(url.searchParams.get('q')).toBe('登录');
    expect(url.searchParams.get('types')).toBe('issue,member');
    expect(url.searchParams.get('limit')).toBe('5');
    expect(url.searchParams.get('cursor')).toBe('cur-0');
    expect(page.data).toEqual([ISSUE_ITEM]);
    expect(page.nextCursor).toBe('cur-1');
  });

  it('缺省可选参数不出现在查询串;携带 AbortSignal', async () => {
    const { fetchImpl, calls } = stubFetch(fakeResponse({ body: { data: [], next_cursor: null } }));
    const client = makeClient(fetchImpl);
    const controller = new AbortController();
    await searchWorkspace(client, 'ws-1', { q: '', signal: controller.signal });
    const url = new URL(calls[0].url);
    expect(url.searchParams.get('q')).toBe('');
    expect(url.searchParams.has('types')).toBe(false);
    expect(url.searchParams.has('limit')).toBe(false);
    expect(calls[0].init?.signal).toBe(controller.signal);
    // GET 不携带 Idempotency-Key(§6.5)
    expect(headersOf(calls[0])['Idempotency-Key']).toBeUndefined();
  });

  it('422 query_cost_exceeded 归一为 MeshApiError(携带 code)', async () => {
    const { fetchImpl } = stubFetch(
      fakeResponse({
        status: 422,
        body: { error: { code: 'query_cost_exceeded', message: 'cost' } },
      }),
    );
    const client = makeClient(fetchImpl);
    await expect(searchWorkspace(client, 'ws-1', { q: 'x' })).rejects.toMatchObject({
      status: 422,
      code: 'query_cost_exceeded',
    });
  });

  it('429 rate_limited 解析 Retry-After', async () => {
    const { fetchImpl } = stubFetch(
      fakeResponse({
        status: 429,
        body: { error: { code: 'rate_limited', message: 'slow down' } },
        headers: { 'Retry-After': '7' },
      }),
    );
    const client = makeClient(fetchImpl);
    try {
      await searchWorkspace(client, 'ws-1', { q: 'x' });
      expect.unreachable('should throw');
    } catch (error) {
      expect(error).toBeInstanceOf(MeshApiError);
      expect((error as MeshApiError).retryAfter).toBe(7);
    }
  });

  it('400 validation_error(非法 types)/ 403 forbidden 透传 code', async () => {
    const { fetchImpl } = stubFetch(
      fakeResponse({ status: 400, body: { error: { code: 'validation_error', message: 'bad' } } }),
    );
    const client = makeClient(fetchImpl);
    await expect(searchWorkspace(client, 'ws-1', { q: 'x' })).rejects.toMatchObject({
      code: 'validation_error',
    });
  });
});

describe('isIdentifierQuery', () => {
  it('完整 KEY-N 形态(前后空白容忍)→ true', () => {
    expect(isIdentifierQuery('WEB-124')).toBe(true);
    expect(isIdentifierQuery('  web-1  ')).toBe(true);
    expect(isIdentifierQuery('A0-9')).toBe(true);
  });

  it('非 identifier(片段/纯数字/多段/空)→ false', () => {
    expect(isIdentifierQuery('WEB-')).toBe(false);
    expect(isIdentifierQuery('-124')).toBe(false);
    expect(isIdentifierQuery('WEB')).toBe(false);
    expect(isIdentifierQuery('WEB-124-9')).toBe(false);
    expect(isIdentifierQuery('登录')).toBe(false);
    expect(isIdentifierQuery('')).toBe(false);
    expect(isIdentifierQuery('1WEB-1')).toBe(false);
  });
});

describe('highlightRangesToSpans(code point 单位,§3.2)', () => {
  it('CJK 标题区间精确映射([0,2) 标注「登录」)', () => {
    expect(highlightRangesToSpans('登录页崩溃', [[0, 2]])).toEqual([
      { text: '登录', marked: true },
      { text: '页崩溃', marked: false },
    ]);
  });

  it('多区间合并相邻分段;中段命中', () => {
    expect(highlightRangesToSpans('Safari 崩溃', [[0, 6], [7, 9]])).toEqual([
      { text: 'Safari', marked: true },
      { text: ' ', marked: false },
      { text: '崩溃', marked: true },
    ]);
  });

  it('代理对表情按 code point 计偏移(非 UTF-16 单元)', () => {
    // '😀x' Array.from → ['😀','x'];区间 [1,2) 标注 'x'
    expect(highlightRangesToSpans('😀x', [[1, 2]])).toEqual([
      { text: '😀', marked: false },
      { text: 'x', marked: true },
    ]);
  });

  it('越界区间钳制到标题长度;空区间返回单未标记分段', () => {
    expect(highlightRangesToSpans('ab', [[-3, 99]])).toEqual([{ text: 'ab', marked: true }]);
    expect(highlightRangesToSpans('ab', [])).toEqual([{ text: 'ab', marked: false }]);
  });

  it('空标题 → 空分段数组', () => {
    expect(highlightRangesToSpans('', [[0, 1]])).toEqual([]);
  });
});

describe('favorites 数据源(§6.19 / §4.2.1)', () => {
  it('listPaletteFavorites 携带 workspace_id 查询', async () => {
    const { fetchImpl, calls } = stubFetch(
      fakeResponse({
        body: { data: [{ target_type: 'issue', target_id: 'i-1', title: 'T' }], next_cursor: null },
      }),
    );
    const client = makeClient(fetchImpl);
    const entries = await listPaletteFavorites(client, 'ws-1');
    expect(new URL(calls[0].url).pathname).toBe('/api/v1/favorites');
    expect(new URL(calls[0].url).searchParams.get('workspace_id')).toBe('ws-1');
    expect(entries).toHaveLength(1);
  });

  it('putFavorite / deleteFavorite 走幂等 PUT/DELETE 目标路径', async () => {
    const { fetchImpl, calls } = stubFetch(fakeResponse({ status: 204 }));
    const client = makeClient(fetchImpl);
    await putFavorite(client, 'chat_session', 's-1');
    await deleteFavorite(client, 'issue', 'i 1');
    expect(calls[0].url).toBe('http://api.test/api/v1/favorites/chat_session/s-1');
    expect(calls[0].init?.method).toBe('PUT');
    expect(calls[1].url).toBe('http://api.test/api/v1/favorites/issue/i%201');
    expect(calls[1].init?.method).toBe('DELETE');
  });

  it('toggleFavoriteForTarget:已在收藏 → DELETE;不在 → PUT', async () => {
    const { fetchImpl, calls } = stubFetch(
      fakeResponse({
        body: { data: [{ target_type: 'issue', target_id: 'i-1' }], next_cursor: null },
      }),
      fakeResponse({ status: 204 }),
    );
    const client = makeClient(fetchImpl);
    const result = await toggleFavoriteForTarget(client, 'ws-1', 'issue', 'i-1');
    expect(result).toBe('removed');
    expect(calls[1].init?.method).toBe('DELETE');

    const second = stubFetch(
      fakeResponse({ body: { data: [], next_cursor: null } }),
      fakeResponse({ status: 204 }),
    );
    const added = await toggleFavoriteForTarget(
      makeClient(second.fetchImpl),
      'ws-1',
      'project',
      'p-9',
    );
    expect(added).toBe('added');
    expect(second.calls[1].init?.method).toBe('PUT');
  });
});
