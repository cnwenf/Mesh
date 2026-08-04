import { afterEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../client';
import { MeshApiError } from '../errors';
import { failingFetch, fakeResponse, headersOf, stubFetch } from './fetchStub';

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function makeClient(fetchImpl: typeof fetch, getToken: () => string | null = () => 'tok') {
  return new MeshApiClient({ baseUrl: 'https://api.mesh.test', getToken, fetchImpl });
}

afterEach(() => {
  vi.useRealTimers();
});

describe('MeshApiClient URL 构造(README §6.14)', () => {
  it('baseUrl + 带前导斜杠的 path', async () => {
    const { fetchImpl, calls } = stubFetch(fakeResponse({ body: { data: {} } }));
    await makeClient(fetchImpl).request('GET', '/api/v1/issues');
    expect(calls[0].url).toBe('https://api.mesh.test/api/v1/issues');
  });

  it('path 不带前导斜杠时自动补齐', async () => {
    const { fetchImpl, calls } = stubFetch(fakeResponse({ body: { data: {} } }));
    await makeClient(fetchImpl).request('GET', 'api/v1/issues');
    expect(calls[0].url).toBe('https://api.mesh.test/api/v1/issues');
  });

  it('baseUrl 尾随斜杠不会产生双斜杠', async () => {
    const { fetchImpl, calls } = stubFetch(fakeResponse({ body: { data: {} } }));
    const client = new MeshApiClient({
      baseUrl: 'https://api.mesh.test/',
      getToken: () => null,
      fetchImpl,
    });
    await client.request('GET', '/x');
    expect(calls[0].url).toBe('https://api.mesh.test/x');
  });

  it('query 序列化字符串/数字/布尔并跳过 undefined', async () => {
    const { fetchImpl, calls } = stubFetch(fakeResponse({ body: { data: {} } }));
    await makeClient(fetchImpl).request('GET', '/x', {
      query: { q: 'a b', limit: 20, archived: false, cursor: undefined },
    });
    const url = new URL(calls[0].url);
    expect(url.searchParams.get('q')).toBe('a b');
    expect(url.searchParams.get('limit')).toBe('20');
    expect(url.searchParams.get('archived')).toBe('false');
    expect(url.searchParams.has('cursor')).toBe(false);
  });

  it('全为 undefined 的 query 不产生查询串', async () => {
    const { fetchImpl, calls } = stubFetch(fakeResponse({ body: { data: {} } }));
    await makeClient(fetchImpl).request('GET', '/x', { query: { a: undefined } });
    expect(calls[0].url).toBe('https://api.mesh.test/x');
  });
});

describe('鉴权头(README §6.14)', () => {
  it('token 非空 → Authorization: Bearer <token>', async () => {
    const { fetchImpl, calls } = stubFetch(fakeResponse({ body: { data: {} } }));
    await makeClient(fetchImpl, () => 'tok_abc').request('GET', '/x');
    expect(headersOf(calls[0]).Authorization).toBe('Bearer tok_abc');
  });

  it('token 为空 → 不带 Authorization(便于登录前调用)', async () => {
    const { fetchImpl, calls } = stubFetch(fakeResponse({ body: { data: {} } }));
    await makeClient(fetchImpl, () => null).request('GET', '/x');
    expect(headersOf(calls[0]).Authorization).toBeUndefined();
  });
});

describe('请求体与 Content-Type', () => {
  it('body 非 undefined → JSON 序列化并置 application/json', async () => {
    const { fetchImpl, calls } = stubFetch(fakeResponse({ body: { data: {} } }));
    await makeClient(fetchImpl).request('POST', '/x', { body: { title: 'hi' } });
    expect(headersOf(calls[0])['Content-Type']).toBe('application/json');
    expect(calls[0].init?.body).toBe(JSON.stringify({ title: 'hi' }));
  });

  it('body 为 undefined → 无 Content-Type、无 body', async () => {
    const { fetchImpl, calls } = stubFetch(fakeResponse({ body: { data: {} } }));
    await makeClient(fetchImpl).request('GET', '/x');
    expect(headersOf(calls[0])['Content-Type']).toBeUndefined();
    expect(calls[0].init?.body).toBeUndefined();
  });
});

describe('幂等键(README §6.5)', () => {
  it('POST 自动携带 UUID 形式的 Idempotency-Key,且每次不同', async () => {
    const { fetchImpl, calls } = stubFetch(fakeResponse({ body: { data: {} } }));
    const client = makeClient(fetchImpl);
    await client.request('POST', '/x', { body: {} });
    await client.request('POST', '/x', { body: {} });
    const first = headersOf(calls[0])['Idempotency-Key'];
    const second = headersOf(calls[1])['Idempotency-Key'];
    expect(first).toMatch(UUID_RE);
    expect(second).toMatch(UUID_RE);
    expect(first).not.toBe(second);
  });

  it.each(['PUT', 'PATCH', 'DELETE'] as const)('%s 自动携带 Idempotency-Key', async (method) => {
    const { fetchImpl, calls } = stubFetch(fakeResponse({ body: { data: {} } }));
    await makeClient(fetchImpl).request(method, '/x', { body: {} });
    expect(headersOf(calls[0])['Idempotency-Key']).toMatch(UUID_RE);
  });

  it('GET 永不携带 Idempotency-Key', async () => {
    const { fetchImpl, calls } = stubFetch(fakeResponse({ body: { data: {} } }));
    await makeClient(fetchImpl).request('GET', '/x');
    expect(headersOf(calls[0])['Idempotency-Key']).toBeUndefined();
  });

  it('显式 idempotencyKey 原样使用', async () => {
    const { fetchImpl, calls } = stubFetch(fakeResponse({ body: { data: {} } }));
    await makeClient(fetchImpl).request('POST', '/x', { body: {}, idempotencyKey: 'fixed-key' });
    expect(headersOf(calls[0])['Idempotency-Key']).toBe('fixed-key');
  });

  it('非安全上下文(HTTP 部署、crypto.randomUUID 缺失)仍自动生成幂等键且请求正常发出(MES-129)', async () => {
    // 故障现场:HTTP 下 crypto.randomUUID 为 undefined,裸调抛 TypeError,
    // fetch 不发出 → 写请求全挂。兜底后应照常携带合法 v4 键并完成请求。
    vi.stubGlobal('crypto', { getRandomValues: crypto.getRandomValues.bind(crypto) });
    const { fetchImpl, calls } = stubFetch(fakeResponse({ body: { data: {} } }));
    await makeClient(fetchImpl).request('POST', '/x', { body: {} });
    expect(headersOf(calls[0])['Idempotency-Key']).toMatch(UUID_RE);
    expect(calls).toHaveLength(1);
  });
});

describe('If-Match 与自定义头/信号', () => {
  it('ifMatch → If-Match 头', async () => {
    const { fetchImpl, calls } = stubFetch(fakeResponse({ body: { data: {} } }));
    await makeClient(fetchImpl).request('PATCH', '/x', {
      body: {},
      ifMatch: '2026-01-01T00:00:00Z',
    });
    expect(headersOf(calls[0])['If-Match']).toBe('2026-01-01T00:00:00Z');
  });

  it('未提供 ifMatch → 无 If-Match 头', async () => {
    const { fetchImpl, calls } = stubFetch(fakeResponse({ body: { data: {} } }));
    await makeClient(fetchImpl).request('GET', '/x');
    expect(headersOf(calls[0])['If-Match']).toBeUndefined();
  });

  it('自定义头合并且绝不就地修改调用方 opts(不可变)', async () => {
    // Arrange
    const { fetchImpl, calls } = stubFetch(fakeResponse({ body: { data: {} } }));
    const customHeaders = Object.freeze({ 'X-Custom': 'a' });
    const opts = Object.freeze({ headers: customHeaders });

    // Act
    await makeClient(fetchImpl).request('GET', '/x', opts);

    // Assert:请求头含自定义值与鉴权头,但原对象未被改动
    const sent = headersOf(calls[0]);
    expect(sent['X-Custom']).toBe('a');
    expect(sent.Authorization).toBe('Bearer tok');
    expect(customHeaders).toEqual({ 'X-Custom': 'a' });
    expect('Authorization' in customHeaders).toBe(false);
  });

  it('AbortSignal 透传给 fetch', async () => {
    const { fetchImpl, calls } = stubFetch(fakeResponse({ body: { data: {} } }));
    const controller = new AbortController();
    await makeClient(fetchImpl).request('GET', '/x', { signal: controller.signal });
    expect(calls[0].init?.signal).toBe(controller.signal);
  });
});

describe('request():单对象包络解析', () => {
  it('2xx 返回 data 字段', async () => {
    const { fetchImpl } = stubFetch(fakeResponse({ body: { data: { id: '1', title: 't' } } }));
    const data = await makeClient(fetchImpl).request<{ id: string; title: string }>('GET', '/x');
    expect(data).toEqual({ id: '1', title: 't' });
  });

  it('data 为 null 时返回 null', async () => {
    const { fetchImpl } = stubFetch(fakeResponse({ rawText: '{"data": null}' }));
    const data = await makeClient(fetchImpl).request<null>('GET', '/x');
    expect(data).toBeNull();
  });

  it('204 返回 undefined 且不解析', async () => {
    const { fetchImpl } = stubFetch(fakeResponse({ status: 204 }));
    const data = await makeClient(fetchImpl).request('DELETE', '/x');
    expect(data).toBeUndefined();
  });

  it('空响应体返回 undefined', async () => {
    const { fetchImpl } = stubFetch(fakeResponse({ rawText: '   ' }));
    const data = await makeClient(fetchImpl).request('GET', '/x');
    expect(data).toBeUndefined();
  });

  it('2xx 缺少 data 形状 → internal_error(携带响应状态)', async () => {
    const { fetchImpl } = stubFetch(fakeResponse({ body: { nope: 1 } }));
    await expect(makeClient(fetchImpl).request('GET', '/x')).rejects.toMatchObject({
      status: 200,
      code: 'internal_error',
    });
  });

  it('2xx 非法 JSON → internal_error', async () => {
    const { fetchImpl } = stubFetch(fakeResponse({ rawText: 'not-json' }));
    await expect(makeClient(fetchImpl).request('GET', '/x')).rejects.toBeInstanceOf(MeshApiError);
  });
});

describe('list():列表包络(原样)', () => {
  it('返回 {data, next_cursor} 原样', async () => {
    const envelope = { data: [{ id: '1' }, { id: '2' }], next_cursor: 'cur_1' };
    const { fetchImpl, calls } = stubFetch(fakeResponse({ body: envelope }));
    const result = await makeClient(fetchImpl).list<{ id: string }>('/items');
    expect(result).toEqual(envelope);
    expect(calls[0].init?.method).toBe('GET');
  });

  it('next_cursor=null(末页)原样保留', async () => {
    const envelope = { data: [], next_cursor: null };
    const { fetchImpl } = stubFetch(fakeResponse({ body: envelope }));
    const result = await makeClient(fetchImpl).list('/items');
    expect(result).toEqual({ data: [], next_cursor: null });
  });

  it('非列表形状(data 非数组)→ internal_error', async () => {
    const { fetchImpl } = stubFetch(fakeResponse({ body: { data: { id: '1' } } }));
    await expect(makeClient(fetchImpl).list('/items')).rejects.toMatchObject({
      code: 'internal_error',
    });
  });

  it('空响应体 → internal_error', async () => {
    const { fetchImpl } = stubFetch(fakeResponse({ rawText: '' }));
    await expect(makeClient(fetchImpl).list('/items')).rejects.toMatchObject({
      code: 'internal_error',
    });
  });
});

describe('grouped():分组整体游标包络(原样,§6.14)', () => {
  it('返回 {groups, next_cursor} 原样', async () => {
    const envelope = {
      groups: [{ key: 'todo', label: 'Todo', count: 3, wip: 5, data: [{ id: '1' }] }],
      next_cursor: 'g_cur',
    };
    const { fetchImpl } = stubFetch(fakeResponse({ body: envelope }));
    const result = await makeClient(fetchImpl).grouped<{ id: string }>('/views/board');
    expect(result).toEqual(envelope);
  });

  it('非分组形状(缺 groups 数组)→ internal_error', async () => {
    const { fetchImpl } = stubFetch(fakeResponse({ body: { data: [] } }));
    await expect(makeClient(fetchImpl).grouped('/views/board')).rejects.toMatchObject({
      code: 'internal_error',
    });
  });

  it('二维投影允许 columns + lanes 代替顶层 groups', async () => {
    const envelope = {
      columns: [{ key: 'todo', label: 'Todo', count: 1, wip: null }],
      lanes: [
        {
          key: 'high',
          label: 'High',
          count: 1,
          groups: [{ key: 'todo', count: 1, data: [{ id: '1' }] }],
        },
      ],
      next_cursor: null,
    };
    const { fetchImpl } = stubFetch(fakeResponse({ body: envelope }));
    const result = await makeClient(fetchImpl).grouped<{ id: string }>('/views/board');
    expect(result).toEqual(envelope);
  });
});

describe('错误信封归一(README §6.14)', () => {
  it('错误信封 → MeshApiError(status/code/message/details)', async () => {
    const { fetchImpl } = stubFetch(
      fakeResponse({
        status: 422,
        body: { error: { code: 'validation_error', message: 'bad', details: { field: 'title' } } },
      }),
    );
    try {
      await makeClient(fetchImpl).request('POST', '/x', { body: {} });
      expect.fail('应当抛出');
    } catch (err) {
      const apiErr = err as MeshApiError;
      expect(apiErr).toBeInstanceOf(MeshApiError);
      expect(apiErr.status).toBe(422);
      expect(apiErr.code).toBe('validation_error');
      expect(apiErr.message).toBe('bad');
      expect(apiErr.details).toEqual({ field: 'title' });
      expect(apiErr.retryAfter).toBeUndefined();
    }
  });

  it('非错误信封的非 2xx → internal_error "HTTP <status>"', async () => {
    const { fetchImpl } = stubFetch(fakeResponse({ status: 500, body: { oops: true } }));
    await expect(makeClient(fetchImpl).request('GET', '/x')).rejects.toMatchObject({
      status: 500,
      code: 'internal_error',
      message: 'HTTP 500',
    });
  });

  it('非 2xx 空体 → internal_error', async () => {
    const { fetchImpl } = stubFetch(fakeResponse({ status: 502, rawText: '' }));
    await expect(makeClient(fetchImpl).request('GET', '/x')).rejects.toMatchObject({
      status: 502,
      code: 'internal_error',
    });
  });

  it('非 2xx 非法 JSON → internal_error', async () => {
    const { fetchImpl } = stubFetch(fakeResponse({ status: 500, rawText: '<html>err</html>' }));
    await expect(makeClient(fetchImpl).request('GET', '/x')).rejects.toMatchObject({
      code: 'internal_error',
    });
  });
});

describe('429 Retry-After 解析', () => {
  it('整数秒 → number', async () => {
    const { fetchImpl } = stubFetch(
      fakeResponse({
        status: 429,
        body: { error: { code: 'rate_limited', message: 'slow' } },
        headers: { 'Retry-After': '30' },
      }),
    );
    await expect(makeClient(fetchImpl).request('GET', '/x')).rejects.toMatchObject({
      status: 429,
      code: 'rate_limited',
      retryAfter: 30,
    });
  });

  it('HTTP-date → 距今秒数(向下取整,最小 0)', async () => {
    // Arrange:固定时钟,Retry-After 指向 30 秒后
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
    const futureDate = new Date('2026-01-01T00:00:30Z').toUTCString();
    const { fetchImpl } = stubFetch(
      fakeResponse({
        status: 429,
        body: { error: { code: 'rate_limited', message: 'slow' } },
        headers: { 'Retry-After': futureDate },
      }),
    );

    // Act / Assert
    await expect(makeClient(fetchImpl).request('GET', '/x')).rejects.toMatchObject({
      retryAfter: 30,
    });
  });

  it('过去的 HTTP-date → 0(最小 0)', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
    const pastDate = new Date('2025-12-31T23:59:00Z').toUTCString();
    const { fetchImpl } = stubFetch(
      fakeResponse({
        status: 429,
        body: { error: { code: 'rate_limited', message: 'slow' } },
        headers: { 'Retry-After': pastDate },
      }),
    );
    await expect(makeClient(fetchImpl).request('GET', '/x')).rejects.toMatchObject({
      retryAfter: 0,
    });
  });

  it('429 无 Retry-After → undefined', async () => {
    const { fetchImpl } = stubFetch(
      fakeResponse({ status: 429, body: { error: { code: 'rate_limited', message: 'slow' } } }),
    );
    await expect(makeClient(fetchImpl).request('GET', '/x')).rejects.toMatchObject({
      retryAfter: undefined,
    });
  });

  it('非 429 即便带 Retry-After 也不解析', async () => {
    const { fetchImpl } = stubFetch(
      fakeResponse({
        status: 503,
        body: { error: { code: 'storage_error', message: 'down' } },
        headers: { 'Retry-After': '30' },
      }),
    );
    await expect(makeClient(fetchImpl).request('GET', '/x')).rejects.toMatchObject({
      retryAfter: undefined,
    });
  });
});

describe('网络失败', () => {
  it('fetch reject → status=0 code=network(不泄漏原始错误)', async () => {
    const client = makeClient(failingFetch());
    try {
      await client.request('GET', '/x');
      expect.fail('应当抛出');
    } catch (err) {
      const apiErr = err as MeshApiError;
      expect(apiErr).toBeInstanceOf(MeshApiError);
      expect(apiErr.status).toBe(0);
      expect(apiErr.code).toBe('network');
      expect(apiErr.message).toBe('network error');
      expect(apiErr.message).not.toContain('boom');
    }
  });
});

describe('默认 fetchImpl', () => {
  it('未注入时使用全局 fetch', async () => {
    // Arrange
    const spy = vi.fn(async () => fakeResponse({ body: { data: { ok: true } } }));
    const original = globalThis.fetch;
    globalThis.fetch = spy as unknown as typeof fetch;
    try {
      const client = new MeshApiClient({ baseUrl: 'https://api.mesh.test', getToken: () => null });
      // Act
      const data = await client.request<{ ok: boolean }>('GET', '/x');
      // Assert
      expect(spy).toHaveBeenCalledTimes(1);
      expect(data).toEqual({ ok: true });
    } finally {
      globalThis.fetch = original;
    }
  });
});

describe('401 全局兜底回调(MES-106)', () => {
  function makeClientWithHook(fetchImpl: typeof fetch, onUnauthorized?: () => void, token = 'tok') {
    return new MeshApiClient({
      baseUrl: 'https://api.mesh.test',
      getToken: () => token,
      fetchImpl,
      onUnauthorized,
    });
  }

  it('受保护端点 401 → 触发回调,且仍照常抛 MeshApiError(401)', async () => {
    const onUnauthorized = vi.fn();
    const { fetchImpl } = stubFetch(
      fakeResponse({
        status: 401,
        body: { error: { code: 'unauthorized', message: 'token expired' } },
      }),
    );
    const client = makeClientWithHook(fetchImpl, onUnauthorized);
    try {
      await client.request('GET', '/api/v1/workspaces');
      expect.fail('应当抛出');
    } catch (err) {
      expect(err).toBeInstanceOf(MeshApiError);
      expect((err as MeshApiError).status).toBe(401);
    }
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it('鉴权豁免端点 401(登录业务错误)→ 不触发回调', async () => {
    const onUnauthorized = vi.fn();
    const { fetchImpl } = stubFetch(
      fakeResponse({
        status: 401,
        body: { error: { code: 'invalid_credentials', message: 'bad' } },
      }),
    );
    const client = makeClientWithHook(fetchImpl, onUnauthorized);
    await expect(client.request('POST', '/api/v1/auth/login', { body: {} })).rejects.toBeInstanceOf(
      MeshApiError,
    );
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it('agent 凭证访问人类专属端点的 401 不清凭证，留给 /me principal 门禁', async () => {
    const onUnauthorized = vi.fn();
    const { fetchImpl } = stubFetch(
      fakeResponse({ status: 401, body: { error: { code: 'unauthorized', message: '' } } }),
    );
    const client = makeClientWithHook(fetchImpl, onUnauthorized, 'mesh_agt_test');

    await expect(client.request('GET', '/api/v1/users/me')).rejects.toBeInstanceOf(MeshApiError);

    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it('agent 凭证的权威 /me 自省返回 401 时仍清凭证', async () => {
    const onUnauthorized = vi.fn();
    const { fetchImpl } = stubFetch(
      fakeResponse({ status: 401, body: { error: { code: 'unauthorized', message: '' } } }),
    );
    const client = makeClientWithHook(fetchImpl, onUnauthorized, 'mesh_agt_revoked');

    await expect(client.request('GET', '/api/v1/me')).rejects.toBeInstanceOf(MeshApiError);

    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it('OAuth 前缀端点 401 → 不触发回调', async () => {
    const onUnauthorized = vi.fn();
    const { fetchImpl } = stubFetch(
      fakeResponse({ status: 401, body: { error: { code: 'x', message: '' } } }),
    );
    const client = makeClientWithHook(fetchImpl, onUnauthorized);
    await expect(client.request('GET', '/api/v1/auth/oauth/mock/callback')).rejects.toBeInstanceOf(
      MeshApiError,
    );
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it('403 / 500 / 429 等非 401 错误 → 不触发回调', async () => {
    for (const status of [403, 404, 429, 500]) {
      const onUnauthorized = vi.fn();
      const { fetchImpl } = stubFetch(
        fakeResponse({ status, body: { error: { code: 'x', message: '' } } }),
      );
      const client = makeClientWithHook(fetchImpl, onUnauthorized);
      await expect(client.request('GET', '/api/v1/workspaces')).rejects.toBeInstanceOf(
        MeshApiError,
      );
      expect(onUnauthorized).not.toHaveBeenCalled();
    }
  });

  it('2xx 成功 → 不触发回调', async () => {
    const onUnauthorized = vi.fn();
    const { fetchImpl } = stubFetch(fakeResponse({ body: { data: {} } }));
    await makeClientWithHook(fetchImpl, onUnauthorized).request('GET', '/api/v1/me');
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it('网络失败(status=0)→ 不触发回调', async () => {
    const onUnauthorized = vi.fn();
    const client = makeClientWithHook(failingFetch(), onUnauthorized);
    await expect(client.request('GET', '/api/v1/workspaces')).rejects.toBeInstanceOf(MeshApiError);
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it('未注入回调时 401 不抛额外错误(仅 MeshApiError)', async () => {
    const { fetchImpl } = stubFetch(
      fakeResponse({ status: 401, body: { error: { code: 'unauthorized', message: '' } } }),
    );
    const client = makeClientWithHook(fetchImpl);
    await expect(client.request('GET', '/api/v1/workspaces')).rejects.toBeInstanceOf(MeshApiError);
  });

  it('列表/分组入口同样接通兜底(共享 execute 路径)', async () => {
    const onUnauthorized = vi.fn();
    const { fetchImpl } = stubFetch(
      fakeResponse({ status: 401, body: { error: { code: 'unauthorized', message: '' } } }),
    );
    const client = makeClientWithHook(fetchImpl, onUnauthorized);
    await expect(client.list('/api/v1/issues')).rejects.toBeInstanceOf(MeshApiError);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    await expect(client.grouped('/api/v1/issues/grouped')).rejects.toBeInstanceOf(MeshApiError);
    expect(onUnauthorized).toHaveBeenCalledTimes(2);
  });
});
