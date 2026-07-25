import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../client';
import { MeshApiError } from '../errors';
import { optimisticUpdate, useOptimisticMutation } from '../optimistic';
import type { OptimisticResult } from '../optimistic';
import { fakeResponse, headersOf, stubFetch } from './fetchStub';

interface Entity {
  id: string;
  title: string;
  updated_at: string;
}

const PATH = '/api/v1/issues/1';
const getServerVersion = (v: Entity): string => v.updated_at;

function scriptedClient(...responses: Response[]) {
  const { fetchImpl, calls } = stubFetch(...responses);
  const client = new MeshApiClient({
    baseUrl: 'https://api.mesh.test',
    getToken: () => 'tok',
    fetchImpl,
  });
  return { client, calls };
}

const CURRENT: Entity = { id: '1', title: 'old', updated_at: 'v1' };

describe('optimisticUpdate(README §6.14/§3.2 乐观并发 + 409 收敛)', () => {
  it('成功:PATCH 携带 changes 与 If-Match(当前版本),conflicted=false', async () => {
    // Arrange
    const saved: Entity = { id: '1', title: 'new', updated_at: 'v2' };
    const { client, calls } = scriptedClient(fakeResponse({ body: { data: saved } }));

    // Act
    const out = await optimisticUpdate<Entity>(client, PATH, {
      current: CURRENT,
      changes: { title: 'new' },
      getServerVersion,
    });

    // Assert
    expect(out).toEqual({ result: saved, conflicted: false });
    expect(calls[0].init?.method).toBe('PATCH');
    expect(headersOf(calls[0])['If-Match']).toBe('v1');
    expect(calls[0].init?.body).toBe(JSON.stringify({ title: 'new' }));
  });

  it('非 409 错误原样上抛,不触发 GET 收敛', async () => {
    // Arrange
    const { client, calls } = scriptedClient(
      fakeResponse({ status: 422, body: { error: { code: 'validation_error', message: 'bad' } } }),
    );

    // Act / Assert
    await expect(
      optimisticUpdate<Entity>(client, PATH, {
        current: CURRENT,
        changes: { title: 'new' },
        getServerVersion,
      }),
    ).rejects.toMatchObject({ status: 422, code: 'validation_error' });
    expect(calls).toHaveLength(1);
  });

  it('409 + onConflict:重拉服务端最新并交由回调收敛,conflicted=true', async () => {
    // Arrange
    const server: Entity = { id: '1', title: 'server', updated_at: 'v2' };
    const { client, calls } = scriptedClient(
      fakeResponse({ status: 409, body: { error: { code: 'conflict', message: 'conflict' } } }),
      fakeResponse({ body: { data: server } }),
    );
    const onConflict = vi.fn(async (latest: Entity, err: MeshApiError) => {
      expect(err).toBeInstanceOf(MeshApiError);
      expect(err.code).toBe('conflict');
      return { ...latest, title: 'merged' };
    });

    // Act
    const out = await optimisticUpdate<Entity>(
      client,
      PATH,
      { current: CURRENT, changes: { title: 'new' }, getServerVersion },
      onConflict,
    );

    // Assert
    expect(out.conflicted).toBe(true);
    expect(out.result).toEqual({ id: '1', title: 'merged', updated_at: 'v2' });
    expect(onConflict).toHaveBeenCalledTimes(1);
    expect(onConflict.mock.calls[0][0]).toEqual(server);
    expect(calls[1].init?.method).toBe('GET');
  });

  it('409 无回调:以服务端最新版本重放一次,conflicted=true', async () => {
    // Arrange
    const server: Entity = { id: '1', title: 'server', updated_at: 'v2' };
    const saved: Entity = { id: '1', title: 'new', updated_at: 'v3' };
    const { client, calls } = scriptedClient(
      fakeResponse({ status: 409, body: { error: { code: 'conflict', message: 'conflict' } } }),
      fakeResponse({ body: { data: server } }),
      fakeResponse({ body: { data: saved } }),
    );

    // Act
    const out = await optimisticUpdate<Entity>(client, PATH, {
      current: CURRENT,
      changes: { title: 'new' },
      getServerVersion,
    });

    // Assert
    expect(out).toEqual({ result: saved, conflicted: true });
    expect(calls).toHaveLength(3);
    expect(headersOf(calls[2])['If-Match']).toBe('v2');
  });

  it('409 重放仍 409 → 二次冲突上抛', async () => {
    // Arrange
    const server: Entity = { id: '1', title: 'server', updated_at: 'v2' };
    const { client } = scriptedClient(
      fakeResponse({ status: 409, body: { error: { code: 'conflict', message: 'conflict' } } }),
      fakeResponse({ body: { data: server } }),
      fakeResponse({ status: 409, body: { error: { code: 'conflict', message: 'conflict' } } }),
    );

    // Act / Assert
    await expect(
      optimisticUpdate<Entity>(client, PATH, {
        current: CURRENT,
        changes: { title: 'new' },
        getServerVersion,
      }),
    ).rejects.toMatchObject({ status: 409, code: 'conflict' });
  });
});

describe('useOptimisticMutation(React 绑定)', () => {
  function deferredResponse() {
    let resolve!: (value: Response) => void;
    const promise = new Promise<Response>((res) => {
      resolve = res;
    });
    return { promise, resolve };
  }

  it('mutate 成功:返回结果,isMutating 在途为 true、完成后 false,lastError=null', async () => {
    // Arrange
    const saved: Entity = { id: '1', title: 'new', updated_at: 'v2' };
    const d = deferredResponse();
    const fetchImpl = vi.fn(async () => d.promise) as unknown as typeof fetch;
    const client = new MeshApiClient({ baseUrl: 'https://api.mesh.test', getToken: () => 'tok', fetchImpl });
    const { result } = renderHook(() =>
      useOptimisticMutation<Entity>({ client, path: PATH, getServerVersion }),
    );

    // Act
    let mutation!: Promise<OptimisticResult<Entity>>;
    act(() => {
      mutation = result.current.mutate(CURRENT, { title: 'new' });
    });
    expect(result.current.isMutating).toBe(true);
    expect(result.current.lastError).toBeNull();

    await act(async () => {
      d.resolve(fakeResponse({ body: { data: saved } }));
      await mutation;
    });

    // Assert
    await waitFor(() => expect(result.current.isMutating).toBe(false));
    expect(await mutation).toEqual({ result: saved, conflicted: false });
    expect(result.current.lastError).toBeNull();
  });

  it('mutate 失败:设置 lastError 并上抛,isMutating 复位', async () => {
    // Arrange
    const { client } = scriptedClient(
      fakeResponse({ status: 422, body: { error: { code: 'validation_error', message: 'bad' } } }),
    );
    const { result } = renderHook(() =>
      useOptimisticMutation<Entity>({ client, path: PATH, getServerVersion }),
    );

    // Act:在 act 内捕获上抛错误,避免状态更新告警
    let thrown: unknown;
    await act(async () => {
      try {
        await result.current.mutate(CURRENT, { title: 'new' });
      } catch (err) {
        thrown = err;
      }
    });

    // Assert
    expect(thrown).toMatchObject({ code: 'validation_error' });
    expect(result.current.isMutating).toBe(false);
    expect(result.current.lastError?.code).toBe('validation_error');
  });

  it('mutate:onConflict 抛出非 MeshApiError 时归一为 network 错误并上抛原错误', async () => {
    // Arrange:409 → 重拉成功 → onConflict 抛出普通错误
    const server: Entity = { id: '1', title: 'server', updated_at: 'v2' };
    const { client } = scriptedClient(
      fakeResponse({ status: 409, body: { error: { code: 'conflict', message: 'conflict' } } }),
      fakeResponse({ body: { data: server } }),
    );
    const onConflict = vi.fn(async () => {
      throw new Error('boom');
    });
    const { result } = renderHook(() =>
      useOptimisticMutation<Entity>({ client, path: PATH, getServerVersion, onConflict }),
    );

    // Act
    let thrown: unknown;
    await act(async () => {
      try {
        await result.current.mutate(CURRENT, { title: 'new' });
      } catch (err) {
        thrown = err;
      }
    });

    // Assert:原错误上抛,lastError 归一为 network
    expect((thrown as Error).message).toBe('boom');
    expect(result.current.lastError?.code).toBe('network');
    expect(result.current.lastError?.status).toBe(0);
  });
});
