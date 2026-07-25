import { describe, expect, it, vi } from 'vitest';
import type { RealtimeFrame } from '../../types/realtime';
import { PollingFallback } from '../pollingFallback';
import type { PollingSource } from '../pollingFallback';

interface Item {
  id: string;
  updated_at?: string;
  title?: string;
}

function createScheduler() {
  const pending: Array<{ fn: () => void; ms: number }> = [];
  const schedule = vi.fn((fn: () => void, ms: number): void => {
    pending.push({ fn, ms });
  });
  return { pending, schedule };
}

function createClock(start = 1000) {
  let t = start;
  return {
    now: (): number => t,
    advance: (ms: number): void => {
      t += ms;
    },
  };
}

async function settle(): Promise<void> {
  for (let i = 0; i < 8; i++) await Promise.resolve();
}

function makeSource(
  impl: (topic: string, since: string | undefined) => Promise<{ items: Item[] }>,
): PollingSource<Item> & { fetch: ReturnType<typeof vi.fn> } {
  const fetch = vi.fn(impl);
  return { fetch };
}

describe('PollingFallback', () => {
  it('starts offline, becomes connected on start, schedules a tick at intervalMs', () => {
    const sched = createScheduler();
    const source = makeSource(async () => ({ items: [] }));
    const pf = new PollingFallback<Item>({ source, intervalMs: 5000, schedule: sched.schedule });
    expect(pf.state).toBe('offline');
    pf.subscribe('view:1');
    pf.start();
    expect(pf.state).toBe('connected');
    expect(sched.pending[0]?.ms).toBe(5000);
  });

  it('uses default intervalMs of 30000', () => {
    const sched = createScheduler();
    const source = makeSource(async () => ({ items: [] }));
    const pf = new PollingFallback<Item>({ source, schedule: sched.schedule });
    pf.start();
    expect(sched.pending[0]?.ms).toBe(30_000);
  });

  it('start() is idempotent (does not double-schedule)', () => {
    const sched = createScheduler();
    const source = makeSource(async () => ({ items: [] }));
    const pf = new PollingFallback<Item>({ source, schedule: sched.schedule });
    pf.start();
    pf.start();
    expect(sched.pending).toHaveLength(1);
  });

  it('polls each subscribed topic with since=undefined first, then the max updated_at', async () => {
    const sched = createScheduler();
    const items: Item[] = [
      { id: 'a', updated_at: '2026-07-25T00:00:05Z' },
      { id: 'b', updated_at: '2026-07-25T00:00:03Z' },
    ];
    const source = makeSource(async () => ({ items }));
    const pf = new PollingFallback<Item>({ source, schedule: sched.schedule });
    pf.subscribe('view:1');
    pf.start();

    sched.pending.pop()?.fn();
    await settle();
    expect(source.fetch).toHaveBeenCalledWith('view:1', undefined);

    sched.pending.pop()?.fn();
    await settle();
    expect(source.fetch).toHaveBeenLastCalledWith('view:1', '2026-07-25T00:00:05Z');
  });

  it('synthesizes RealtimeFrame-shaped objects with incrementing per-topic seq and default type', async () => {
    const sched = createScheduler();
    const items: Item[] = [
      { id: 'a', updated_at: '2026-07-25T00:00:05Z', title: 'x' },
      { id: 'b', updated_at: '2026-07-25T00:00:03Z', title: 'y' },
    ];
    const source = makeSource(async () => ({ items }));
    const pf = new PollingFallback<Item>({ source, schedule: sched.schedule });
    const onFrame = vi.fn();
    pf.onFrame(onFrame);
    pf.subscribe('view:1');
    pf.start();

    sched.pending.pop()?.fn();
    await settle();

    expect(onFrame).toHaveBeenCalledTimes(2);
    const frames = onFrame.mock.calls.map((c) => c[0] as RealtimeFrame);
    expect(frames[0]).toMatchObject({ seq: 1, type: 'poll.sync', topic: 'view:1', ts: '2026-07-25T00:00:05Z', data: items[0] });
    expect(frames[1]).toMatchObject({ seq: 2, type: 'poll.sync', topic: 'view:1', ts: '2026-07-25T00:00:03Z', data: items[1] });
  });

  it('continues seq across polls for the same topic', async () => {
    const sched = createScheduler();
    const source = makeSource(async () => ({ items: [{ id: 'a', updated_at: '2026-07-25T00:00:01Z' }] }));
    const pf = new PollingFallback<Item>({ source, schedule: sched.schedule });
    const onFrame = vi.fn();
    pf.onFrame(onFrame);
    pf.subscribe('view:1');
    pf.start();

    sched.pending.pop()?.fn();
    await settle();
    sched.pending.pop()?.fn();
    await settle();

    const seqs = onFrame.mock.calls.map((c) => (c[0] as RealtimeFrame).seq);
    expect(seqs).toEqual([1, 2]);
  });

  it('honors a custom eventType', async () => {
    const sched = createScheduler();
    const source = makeSource(async () => ({ items: [{ id: 'a' }] }));
    const pf = new PollingFallback<Item>({ source, schedule: sched.schedule, eventType: 'issue.updated' });
    const onFrame = vi.fn();
    pf.onFrame(onFrame);
    pf.subscribe('view:1');
    pf.start();
    sched.pending.pop()?.fn();
    await settle();
    expect((onFrame.mock.calls[0][0] as RealtimeFrame).type).toBe('issue.updated');
  });

  it('falls back to now() for ts when the item has no updated_at', async () => {
    const sched = createScheduler();
    const clock = createClock(123_456);
    const source = makeSource(async () => ({ items: [{ id: 'a' }] }));
    const pf = new PollingFallback<Item>({ source, schedule: sched.schedule, now: clock.now });
    const onFrame = vi.fn();
    pf.onFrame(onFrame);
    pf.subscribe('view:1');
    pf.start();
    sched.pending.pop()?.fn();
    await settle();
    const frame = onFrame.mock.calls[0][0] as RealtimeFrame;
    expect(frame.ts).toBe(new Date(123_456).toISOString());
  });

  it('surfaces fetch errors via onError, stays connected, and retries next tick', async () => {
    const sched = createScheduler();
    const source = makeSource(async () => {
      throw new Error('network down');
    });
    const pf = new PollingFallback<Item>({ source, schedule: sched.schedule });
    const onError = vi.fn();
    const onFrame = vi.fn();
    pf.onError(onError);
    pf.onFrame(onFrame);
    pf.subscribe('view:1');
    pf.start();

    sched.pending.pop()?.fn();
    await settle();

    expect(onError).toHaveBeenCalledTimes(1);
    expect(onFrame).not.toHaveBeenCalled();
    expect(pf.state).toBe('connected');
    expect(sched.pending.length).toBeGreaterThan(0); // next tick scheduled
  });

  it('stops polling an unsubscribed topic', async () => {
    const sched = createScheduler();
    const source = makeSource(async () => ({ items: [] }));
    const pf = new PollingFallback<Item>({ source, schedule: sched.schedule });
    pf.subscribe('a');
    pf.subscribe('b');
    pf.start();
    pf.unsubscribe('b');

    sched.pending.pop()?.fn();
    await settle();

    expect(source.fetch).toHaveBeenCalledWith('a', undefined);
    expect(source.fetch).not.toHaveBeenCalledWith('b', undefined);
  });

  it('stop() goes offline and cancels pending ticks', async () => {
    const sched = createScheduler();
    const source = makeSource(async () => ({ items: [] }));
    const pf = new PollingFallback<Item>({ source, schedule: sched.schedule });
    pf.subscribe('view:1');
    pf.start();
    pf.stop();
    expect(pf.state).toBe('offline');

    const cancelled = sched.pending.pop();
    cancelled?.fn();
    await settle();
    expect(source.fetch).not.toHaveBeenCalled();
  });

  it('onState and onError listeners can unsubscribe', () => {
    const sched = createScheduler();
    const source = makeSource(async () => ({ items: [] }));
    const pf = new PollingFallback<Item>({ source, schedule: sched.schedule });
    const onState = vi.fn();
    const onError = vi.fn();
    const offState = pf.onState(onState);
    const offError = pf.onError(onError);
    offState();
    offError();
    pf.start();
    expect(onState).not.toHaveBeenCalled();
  });

  it('onFrame listener can unsubscribe', async () => {
    const sched = createScheduler();
    const source = makeSource(async () => ({ items: [{ id: 'a', updated_at: '2026-07-25T00:00:01Z' }] }));
    const pf = new PollingFallback<Item>({ source, schedule: sched.schedule });
    const onFrame = vi.fn();
    const off = pf.onFrame(onFrame);
    off();
    pf.subscribe('view:1');
    pf.start();
    sched.pending.pop()?.fn();
    await settle();
    expect(onFrame).not.toHaveBeenCalled();
  });

  it('a throwing frame listener does not break other listeners', async () => {
    const sched = createScheduler();
    const source = makeSource(async () => ({ items: [{ id: 'a', updated_at: '2026-07-25T00:00:01Z' }] }));
    const pf = new PollingFallback<Item>({ source, schedule: sched.schedule });
    const bad = vi.fn(() => {
      throw new Error('listener boom');
    });
    const good = vi.fn();
    pf.onFrame(bad);
    pf.onFrame(good);
    pf.subscribe('view:1');
    pf.start();
    sched.pending.pop()?.fn();
    await settle();
    expect(bad).toHaveBeenCalled();
    expect(good).toHaveBeenCalled();
  });

  it('works with default schedule and now (real timers)', () => {
    const source = makeSource(async () => ({ items: [] }));
    const pf = new PollingFallback<Item>({ source, intervalMs: 1 });
    pf.start();
    expect(pf.state).toBe('connected');
    pf.stop();
    expect(pf.state).toBe('offline');
  });
});
