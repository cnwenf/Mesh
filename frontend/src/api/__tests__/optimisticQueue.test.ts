/**
 * optimisticQueue 单元测试(L182,README §6.12 offline 行「乐观操作排队」)。
 * 覆盖:离线入队/在线直执/network 错误入队/其余错误上抛;FIFO 回放与逐项
 * 结果标记;重试上限;容量护栏;订阅/移除/清空/释放;触发器接线与拆卸。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiError } from '../errors';
import {
  createOptimisticQueue,
  defaultIsOffline,
  initOptimisticQueueTriggers,
  isNetworkError,
} from '../optimisticQueue';

function networkError(): MeshApiError {
  return new MeshApiError({ status: 0, code: 'network', message: 'network error' });
}

function conflictError(): MeshApiError {
  return new MeshApiError({ status: 409, code: 'conflict', message: 'conflict' });
}

describe('isNetworkError / defaultIsOffline', () => {
  it('status 0 或 code network 判为网络错误;其余否', () => {
    expect(isNetworkError(networkError())).toBe(true);
    expect(isNetworkError(new MeshApiError({ status: 500, code: 'network', message: 'x' }))).toBe(
      true,
    );
    expect(isNetworkError(conflictError())).toBe(false);
    expect(isNetworkError(new Error('boom'))).toBe(false);
    expect(isNetworkError(null)).toBe(false);
  });

  it('defaultIsOffline 跟随 navigator.onLine', () => {
    const original = navigator.onLine;
    try {
      Object.defineProperty(navigator, 'onLine', { value: false, configurable: true });
      expect(defaultIsOffline()).toBe(true);
      Object.defineProperty(navigator, 'onLine', { value: true, configurable: true });
      expect(defaultIsOffline()).toBe(false);
    } finally {
      Object.defineProperty(navigator, 'onLine', { value: original, configurable: true });
    }
  });
});

describe('createOptimisticQueue submit', () => {
  it('在线时直接执行并返回 executed(队列保持空)', async () => {
    const queue = createOptimisticQueue({ isOffline: () => false });
    const run = vi.fn(async () => undefined);

    await expect(queue.submit('改状态', run)).resolves.toBe('executed');
    expect(run).toHaveBeenCalledTimes(1);
    expect(queue.items()).toHaveLength(0);
    expect(queue.pendingCount()).toBe(0);
  });

  it('离线时入队返回 queued,不执行操作', async () => {
    const queue = createOptimisticQueue({ isOffline: () => true });
    const run = vi.fn(async () => undefined);

    await expect(queue.submit('改状态', run)).resolves.toBe('queued');
    expect(run).not.toHaveBeenCalled();
    expect(queue.items()).toHaveLength(1);
    expect(queue.items()[0]).toMatchObject({ label: '改状态', status: 'queued', attempts: 0 });
    expect(queue.pendingCount()).toBe(1);
  });

  it('在线执行遇 network 错误 → 转入队列(queued)', async () => {
    const queue = createOptimisticQueue({ isOffline: () => false });
    const run = vi.fn(async () => {
      throw networkError();
    });

    await expect(queue.submit('改状态', run)).resolves.toBe('queued');
    expect(queue.items()[0]).toMatchObject({ status: 'queued' });
  });

  it('在线执行遇非 network 错误 → 上抛且不入队(调用方收敛)', async () => {
    const queue = createOptimisticQueue({ isOffline: () => false });
    const conflict = conflictError();
    const run = vi.fn(async () => {
      throw conflict;
    });

    await expect(queue.submit('改状态', run)).rejects.toBe(conflict);
    expect(queue.items()).toHaveLength(0);
  });
});

describe('createOptimisticQueue flush', () => {
  it('离线时 flush 为空操作(不执行任何排队项)', async () => {
    const queue = createOptimisticQueue({ isOffline: () => true });
    const run = vi.fn(async () => undefined);
    await queue.submit('A', run);

    const summary = await queue.flush();
    expect(run).not.toHaveBeenCalled();
    expect(summary).toEqual({ succeeded: 0, failed: 0, remaining: 1 });
  });

  it('按 FIFO 回放并移除成功项(逐项标记)', async () => {
    let offline = true;
    const queue = createOptimisticQueue({ isOffline: () => offline });
    const order: string[] = [];
    await queue.submit('A', async () => {
      order.push('A');
    });
    await queue.submit('B', async () => {
      order.push('B');
    });
    offline = false;

    const summary = await queue.flush();
    expect(order).toEqual(['A', 'B']);
    expect(summary).toEqual({ succeeded: 2, failed: 0, remaining: 0 });
    expect(queue.items()).toHaveLength(0);
  });

  it('回放中 network 失败 → 回 queued 且 attempts+1,下轮可再试', async () => {
    let offline = true;
    let failOnce = true;
    const queue = createOptimisticQueue({ isOffline: () => offline });
    await queue.submit('A', async () => {
      if (failOnce) throw networkError();
    });
    offline = false;

    const first = await queue.flush();
    expect(first).toEqual({ succeeded: 0, failed: 0, remaining: 1 });
    expect(queue.items()[0]).toMatchObject({ status: 'queued', attempts: 1 });

    failOnce = false;
    const second = await queue.flush();
    expect(second).toEqual({ succeeded: 1, failed: 0, remaining: 0 });
    expect(queue.items()).toHaveLength(0);
  });

  it('达到 maxAttempts 后 network 失败标 failed 不再重试', async () => {
    const queue = createOptimisticQueue({
      isOffline: () => false,
      maxAttempts: 2,
    });
    const run = vi.fn(async () => {
      throw networkError();
    });
    // 在线 submit 遇 network 错误 → 入队(attempts 0)
    await expect(queue.submit('A', run)).resolves.toBe('queued');
    expect(queue.items()).toHaveLength(1);

    await queue.flush(); // attempts 1 → 仍 network → queued
    expect(queue.items()[0]).toMatchObject({ status: 'queued', attempts: 1 });
    await queue.flush(); // attempts 2 ≥ maxAttempts → failed
    expect(queue.items()[0]).toMatchObject({ status: 'failed', attempts: 2 });
    expect(queue.items()[0]?.error).toBeInstanceOf(MeshApiError);

    const summary = await queue.flush(); // failed 项不再回放
    expect(summary.succeeded).toBe(0);
    expect(run).toHaveBeenCalledTimes(3); // submit 1 次 + flush 2 次
  });

  it('回放中非 network 失败 → 立即标 failed 并保留错误', async () => {
    let offline = true;
    const queue = createOptimisticQueue({ isOffline: () => offline });
    const conflict = conflictError();
    await queue.submit('A', async () => {
      throw conflict;
    });
    offline = false;

    const summary = await queue.flush();
    expect(summary).toEqual({ succeeded: 0, failed: 1, remaining: 0 });
    expect(queue.items()[0]).toMatchObject({ status: 'failed', error: conflict });
  });

  it('并发 flush 不重复回放(第二路返回空汇总)', async () => {
    let offline = true;
    const queue = createOptimisticQueue({ isOffline: () => offline });
    let resolveRun: () => void = () => undefined;
    await queue.submit(
      'A',
      () =>
        new Promise<void>((resolve) => {
          resolveRun = () => resolve();
        }),
    );
    offline = false;

    const first = queue.flush();
    const second = await queue.flush(); // 正在回放 → 空操作
    expect(second).toEqual({ succeeded: 0, failed: 0, remaining: 1 });
    resolveRun();
    const summary = await first;
    expect(summary.succeeded).toBe(1);
  });
});

describe('createOptimisticQueue 订阅与清理', () => {
  it('subscribe 在入队/回放/移除时收到快照,退订后不再通知', async () => {
    let offline = true;
    const queue = createOptimisticQueue({ isOffline: () => offline });
    const seen: unknown[][] = [];
    const unsubscribe = queue.subscribe((items) => seen.push(items.map((i) => i.status)));

    await queue.submit('A', async () => undefined);
    expect(seen).toHaveLength(1);
    offline = false;
    await queue.flush();
    // running 通知 + 终态通知
    expect(seen.length).toBeGreaterThanOrEqual(3);
    unsubscribe();
    await queue.submit('B', async () => undefined);
    const countAfterUnsub = seen.length;
    await queue.flush();
    expect(seen).toHaveLength(countAfterUnsub);
  });

  it('remove 移除失败条目;不存在即空操作', async () => {
    let offline = true;
    const queue = createOptimisticQueue({ isOffline: () => offline });
    await queue.submit('A', async () => {
      throw conflictError();
    });
    offline = false;
    await queue.flush();
    const failed = queue.items()[0];
    expect(failed?.status).toBe('failed');

    queue.remove('not-there');
    expect(queue.items()).toHaveLength(1);
    queue.remove(failed!.id);
    expect(queue.items()).toHaveLength(0);
  });

  it('clear 清空条目并通知', async () => {
    const queue = createOptimisticQueue({ isOffline: () => true });
    const seen: number[] = [];
    queue.subscribe((items) => seen.push(items.length));
    await queue.submit('A', async () => undefined);
    queue.clear();
    expect(queue.items()).toHaveLength(0);
    expect(seen).toEqual([1, 0]);
    queue.clear(); // 空队列空操作,不重复通知
    expect(seen).toEqual([1, 0]);
  });

  it('dispose 清空条目与监听(队列语义仍安全)', async () => {
    const queue = createOptimisticQueue({ isOffline: () => true });
    const seen: unknown[] = [];
    queue.subscribe((items) => seen.push(items));
    await queue.submit('A', async () => undefined);
    queue.dispose();
    expect(queue.items()).toHaveLength(0);
    queue.clear(); // dispose 后调用不抛错
    expect(seen).toHaveLength(1); // 监听已清空,不再收到通知
  });

  it('容量护栏:超过 maxQueueSize 丢弃最旧条目', async () => {
    const queue = createOptimisticQueue({ isOffline: () => true, maxQueueSize: 2 });
    await queue.submit('A', async () => undefined);
    await queue.submit('B', async () => undefined);
    await queue.submit('C', async () => undefined);
    expect(queue.items().map((item) => item.label)).toEqual(['B', 'C']);
  });
});

describe('initOptimisticQueueTriggers', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('window online 事件触发回放', async () => {
    let offline = true;
    const queue = createOptimisticQueue({ isOffline: () => offline });
    const run = vi.fn(async () => undefined);
    await queue.submit('A', run);
    const dispose = initOptimisticQueueTriggers(queue);

    offline = false;
    window.dispatchEvent(new Event('online'));
    await vi.waitFor(() => expect(run).toHaveBeenCalledTimes(1));
    expect(queue.items()).toHaveLength(0);
    dispose();
  });

  it('extraTriggers(如 realtime 重连)触发回放', async () => {
    let offline = true;
    const queue = createOptimisticQueue({ isOffline: () => offline });
    const run = vi.fn(async () => undefined);
    await queue.submit('A', run);
    let fireFn: () => void = () => undefined;
    let teardownCalls = 0;
    const dispose = initOptimisticQueueTriggers(queue, {
      listenOnline: false,
      extraTriggers: [
        (fire) => {
          fireFn = fire;
          return () => {
            teardownCalls += 1;
          };
        },
      ],
    });

    offline = false;
    fireFn();
    await vi.waitFor(() => expect(run).toHaveBeenCalledTimes(1));
    dispose();
    expect(teardownCalls).toBe(1); // 拆卸函数已解绑
  });

  it('拆卸后 online 事件不再触发回放', async () => {
    let offline = true;
    const queue = createOptimisticQueue({ isOffline: () => offline });
    const run = vi.fn(async () => undefined);
    await queue.submit('A', run);
    const dispose = initOptimisticQueueTriggers(queue);
    dispose();

    offline = false;
    window.dispatchEvent(new Event('online'));
    await Promise.resolve();
    expect(run).not.toHaveBeenCalled();
    expect(queue.items()).toHaveLength(1);
  });
});
