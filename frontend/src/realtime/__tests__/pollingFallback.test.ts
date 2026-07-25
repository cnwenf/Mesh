import { describe, expect, it, vi } from 'vitest';
import type { RealtimeEventFrame } from '../../types/realtime';
import { PollingFallback } from '../pollingFallback';
import type { PollingSource } from '../pollingFallback';

function createScheduler() {
  const pending: Array<{ fn: () => void; ms: number }> = [];
  const schedule = vi.fn((fn: () => void, ms: number): void => {
    pending.push({ fn, ms });
  });
  return { pending, schedule };
}

async function settle(): Promise<void> {
  for (let i = 0; i < 8; i++) await Promise.resolve();
}

function frame(
  seq: number,
  channel = 'view:1',
  payload: Record<string, unknown> = { id: 'x' },
): RealtimeEventFrame {
  return { op: 'event', channel, seq, event: 'issue.updated', payload };
}

function makeSource(
  impl: (channel: string, since: number) => Promise<{ frames: RealtimeEventFrame[] }>,
): PollingSource & { fetch: ReturnType<typeof vi.fn> } {
  const fetch = vi.fn(impl);
  return { fetch };
}

describe('PollingFallback(§3.2 离线降级轮询)', () => {
  it('starts offline, becomes connected on start, schedules a tick at intervalMs', () => {
    const sched = createScheduler();
    const source = makeSource(async () => ({ frames: [] }));
    const pf = new PollingFallback({ source, intervalMs: 5000, schedule: sched.schedule });
    expect(pf.state).toBe('offline');
    pf.subscribe('view:1');
    pf.start();
    expect(pf.state).toBe('connected');
    expect(sched.pending[0]?.ms).toBe(5000);
  });

  it('uses default intervalMs of 30000 (kanban §3.5)', () => {
    const sched = createScheduler();
    const source = makeSource(async () => ({ frames: [] }));
    const pf = new PollingFallback({ source, schedule: sched.schedule });
    pf.start();
    expect(sched.pending[0]?.ms).toBe(30_000);
  });

  it('start() is idempotent (does not double-schedule)', () => {
    const sched = createScheduler();
    const source = makeSource(async () => ({ frames: [] }));
    const pf = new PollingFallback({ source, schedule: sched.schedule });
    pf.start();
    pf.start();
    expect(sched.pending).toHaveLength(1);
  });

  it('polls each channel with since=0 first, then the max seen seq', async () => {
    const sched = createScheduler();
    const frames = [frame(3), frame(5)];
    const source = makeSource(async () => ({ frames }));
    const pf = new PollingFallback({ source, schedule: sched.schedule });
    pf.subscribe('view:1');
    pf.start();

    sched.pending.pop()?.fn();
    await settle();
    expect(source.fetch).toHaveBeenCalledWith('view:1', 0);

    sched.pending.pop()?.fn();
    await settle();
    expect(source.fetch).toHaveBeenLastCalledWith('view:1', 5);
  });

  it('dispatches real event frames and tracks per-channel watermarks independently', async () => {
    const sched = createScheduler();
    const source = makeSource(async (channel) =>
      channel === 'a' ? { frames: [frame(2, 'a')] } : { frames: [frame(9, 'b')] },
    );
    const pf = new PollingFallback({ source, schedule: sched.schedule });
    const onFrame = vi.fn();
    pf.onFrame(onFrame);
    pf.subscribe('a');
    pf.subscribe('b');
    pf.start();

    sched.pending.pop()?.fn();
    await settle();
    expect(onFrame).toHaveBeenCalledTimes(2);
    expect(onFrame).toHaveBeenCalledWith(expect.objectContaining({ channel: 'a', seq: 2 }));
    expect(onFrame).toHaveBeenCalledWith(expect.objectContaining({ channel: 'b', seq: 9 }));

    // 第二轮按各自水位拉取
    sched.pending.pop()?.fn();
    await settle();
    expect(source.fetch).toHaveBeenCalledWith('a', 2);
    expect(source.fetch).toHaveBeenCalledWith('b', 9);
  });

  it('seedSince 以 WS 游标初始化水位(仅前进)', async () => {
    const sched = createScheduler();
    const source = makeSource(async () => ({ frames: [] }));
    const pf = new PollingFallback({ source, schedule: sched.schedule });
    pf.subscribe('view:1');
    pf.seedSince('view:1', 41);
    pf.seedSince('view:1', 10); // 更小的值不回退
    pf.start();
    sched.pending.pop()?.fn();
    await settle();
    expect(source.fetch).toHaveBeenCalledWith('view:1', 41);
  });

  it('source 出错不停机,经 onError 上报,下一拍继续', async () => {
    const sched = createScheduler();
    let calls = 0;
    const source = makeSource(async () => {
      calls += 1;
      if (calls === 1) throw new Error('network down');
      return { frames: [frame(1)] };
    });
    const pf = new PollingFallback({ source, schedule: sched.schedule });
    const onError = vi.fn();
    const onFrame = vi.fn();
    pf.onError(onError);
    pf.onFrame(onFrame);
    pf.subscribe('view:1');
    pf.start();
    expect(pf.state).toBe('connected');

    sched.pending.pop()?.fn();
    await settle();
    expect(onError).toHaveBeenCalledTimes(1);
    expect(pf.state).toBe('connected'); // 不降级停机

    sched.pending.pop()?.fn();
    await settle();
    expect(onFrame).toHaveBeenCalledTimes(1);
  });

  it('unsubscribe 后不再轮询该频道', async () => {
    const sched = createScheduler();
    const source = makeSource(async () => ({ frames: [] }));
    const pf = new PollingFallback({ source, schedule: sched.schedule });
    pf.subscribe('view:1');
    pf.unsubscribe('view:1');
    pf.start();
    sched.pending.pop()?.fn();
    await settle();
    expect(source.fetch).not.toHaveBeenCalled();
  });

  it('stop() → offline,挂起定时器失效且不再轮询', async () => {
    const sched = createScheduler();
    const source = makeSource(async () => ({ frames: [] }));
    const pf = new PollingFallback({ source, schedule: sched.schedule });
    const onState = vi.fn();
    pf.onState(onState);
    pf.subscribe('view:1');
    pf.start();
    pf.stop();
    expect(pf.state).toBe('offline');
    expect(onState).toHaveBeenLastCalledWith('offline');
    const timer = sched.pending.pop();
    timer?.fn();
    await settle();
    expect(source.fetch).not.toHaveBeenCalled();
  });

  it('onFrame/onState/onError 监听器可取消订阅', async () => {
    const sched = createScheduler();
    const source = makeSource(async () => ({ frames: [frame(1)] }));
    const pf = new PollingFallback({ source, schedule: sched.schedule });
    const onFrame = vi.fn();
    const off = pf.onFrame(onFrame);
    off();
    pf.subscribe('view:1');
    pf.start();
    sched.pending.pop()?.fn();
    await settle();
    expect(onFrame).not.toHaveBeenCalled();
  });

  it('低于水位的帧不回退 seq 水位(水位仅前进)', async () => {
    const sched = createScheduler();
    const source = makeSource(async () => ({ frames: [frame(3)] }));
    const pf = new PollingFallback({ source, schedule: sched.schedule });
    pf.subscribe('view:1');
    pf.seedSince('view:1', 10);
    pf.start();
    sched.pending.pop()?.fn();
    await settle();
    // 下一拍仍以原水位 10 拉取(帧 seq=3 不回退水位)
    sched.pending.pop()?.fn();
    await settle();
    expect(source.fetch).toHaveBeenLastCalledWith('view:1', 10);
  });

  it('监听器抛错不影响轮询继续', async () => {
    const sched = createScheduler();
    const source = makeSource(async () => ({ frames: [frame(1), frame(2)] }));
    const pf = new PollingFallback({ source, schedule: sched.schedule });
    const good = vi.fn();
    pf.onFrame(() => {
      throw new Error('listener bug');
    });
    pf.onFrame(good);
    pf.subscribe('view:1');
    pf.start();
    sched.pending.pop()?.fn();
    await settle();
    expect(good).toHaveBeenCalledTimes(2);
  });
});
