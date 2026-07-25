import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AUTH_SUBPROTOCOL } from '../../types/realtime';
import { RealtimeClient } from '../RealtimeClient';
import type { RealtimeClientOptions } from '../RealtimeClient';
import { FakeWebSocket, FakeWebSocketImpl } from './FakeWebSocket';

const URL = 'ws://host/ws';
const TS = '2026-07-25T00:00:00Z';

function createScheduler() {
  const pending: Array<{ fn: () => void; ms: number }> = [];
  const schedule = vi.fn((fn: () => void, ms: number): void => {
    pending.push({ fn, ms });
  });
  function flush(): void {
    const n = pending.length;
    for (let i = 0; i < n; i++) pending.shift()?.fn();
  }
  return { pending, schedule, flush };
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

/** flush a few microtasks so resolved/rejected promise chains settle */
async function settle(): Promise<void> {
  for (let i = 0; i < 6; i++) await Promise.resolve();
}

function makeClient(overrides: Partial<RealtimeClientOptions> = {}): {
  client: RealtimeClient;
  sched: ReturnType<typeof createScheduler>;
  clock: ReturnType<typeof createClock>;
} {
  const sched = createScheduler();
  const clock = createClock();
  const client = new RealtimeClient({
    url: URL,
    getToken: () => 'token-123',
    WebSocketImpl: FakeWebSocketImpl,
    schedule: sched.schedule,
    now: clock.now,
    ...overrides,
  });
  liveClients.push(client);
  return { client, sched, clock };
}

/** construct + connect + open, returning the live socket */
function connectClient(overrides: Partial<RealtimeClientOptions> = {}) {
  const { client, sched, clock } = makeClient(overrides);
  client.connect();
  const socket = FakeWebSocket.last;
  socket.open();
  return { client, socket, sched, clock };
}

function dataFrame(seq: number, topic: string, data: Record<string, unknown> = { id: 'x' }) {
  return { seq, type: 'issue.updated', topic, ts: TS, data };
}

/** 统一回收每个用例创建的 client:避免 window online/offline 监听器跨用例泄漏 */
const liveClients: RealtimeClient[] = [];

beforeEach(() => {
  FakeWebSocket.reset();
  localStorage.clear();
});

afterEach(() => {
  for (const client of liveClients.splice(0)) {
    client.disconnect();
  }
});

describe('subprotocol auth (§6.16)', () => {
  it('opens the socket with the auth subprotocol and token, url verbatim', () => {
    const { socket } = connectClient();
    expect(socket.url).toBe(URL);
    expect(socket.url).not.toContain('?');
    expect(socket.protocols).toEqual([AUTH_SUBPROTOCOL, 'token-123']);
  });

  it('never appends the token to the url query', () => {
    const { socket } = connectClient();
    expect(socket.url.toLowerCase()).not.toContain('token');
  });

  it('goes offline and opens no socket when token is null', () => {
    const { client } = makeClient({ getToken: () => null });
    client.connect();
    expect(client.state).toBe('offline');
    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it('goes offline and opens no socket when token is empty string', () => {
    const { client } = makeClient({ getToken: () => '' });
    client.connect();
    expect(client.state).toBe('offline');
    expect(FakeWebSocket.instances).toHaveLength(0);
  });
});

describe('connection lifecycle', () => {
  it('starts idle, transitions connecting → connected on open', () => {
    const { client } = makeClient();
    expect(client.state).toBe('idle');
    client.connect();
    expect(client.state).toBe('connecting');
    FakeWebSocket.last.open();
    expect(client.state).toBe('connected');
  });

  it('does not open a second socket when connect() is called twice', () => {
    const { client } = connectClient();
    client.connect();
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it('works with default schedule and now (real timers)', () => {
    const client = new RealtimeClient({
      url: URL,
      getToken: () => 'token-123',
      WebSocketImpl: FakeWebSocketImpl,
    });
    client.connect();
    FakeWebSocket.last.open();
    expect(client.state).toBe('connected');
    client.disconnect();
    expect(client.state).toBe('idle');
  });
});

describe('subscribe / unsubscribe', () => {
  it('sends {op:subscribe,topic} without resume_from when no cursor exists', () => {
    const { client, socket } = connectClient();
    client.subscribe('topicA');
    expect(socket.sentOps()).toContainEqual({ op: 'subscribe', topic: 'topicA' });
  });

  it('queues subscriptions issued before connect and sends them on open', () => {
    const { client } = makeClient();
    client.subscribe('topicA');
    client.connect();
    const socket = FakeWebSocket.last;
    socket.open();
    expect(socket.sentOps()).toContainEqual({ op: 'subscribe', topic: 'topicA' });
  });

  it('includes resume_from = last_seq + 1 when a cursor exists', () => {
    const { client, socket } = connectClient();
    client.subscribe('topicA');
    socket.message(dataFrame(41, 'topicA'));
    client.subscribe('topicA');
    expect(socket.sentOps()).toContainEqual({ op: 'subscribe', topic: 'topicA', resume_from: 42 });
  });

  it('unsubscribe sends {op:unsubscribe}', () => {
    const { client, socket } = connectClient();
    client.subscribe('topicA');
    client.unsubscribe('topicA');
    expect(socket.sentOps()).toContainEqual({ op: 'unsubscribe', topic: 'topicA' });
  });

  it('does not re-subscribe a forgotten topic after reconnect', () => {
    const { client, socket, sched } = connectClient();
    client.subscribe('keep');
    client.subscribe('drop');
    client.unsubscribe('drop');
    socket.emitClose();
    sched.pending.pop()?.fn();
    const socket2 = FakeWebSocket.last;
    socket2.open();
    const topics = socket2.sentOps().map((op) => op.topic);
    expect(topics).toContain('keep');
    expect(topics).not.toContain('drop');
  });
});

describe('data frames (at-least-once idempotency)', () => {
  it('updates the cursor and dispatches frames with seq greater than cursor', () => {
    const { client, socket } = connectClient();
    const onFrame = vi.fn();
    client.onFrame(onFrame);
    client.subscribe('t');
    socket.message(dataFrame(5, 't'));
    expect(onFrame).toHaveBeenCalledTimes(1);
    expect(onFrame).toHaveBeenCalledWith(expect.objectContaining({ seq: 5, topic: 't' }));
  });

  it('drops replayed frames with seq <= cursor without dispatching', () => {
    const { client, socket } = connectClient();
    const onFrame = vi.fn();
    client.onFrame(onFrame);
    client.subscribe('t');
    socket.message(dataFrame(5, 't'));
    socket.message(dataFrame(5, 't'));
    socket.message(dataFrame(3, 't'));
    expect(onFrame).toHaveBeenCalledTimes(1);
  });

  it('tracks cursors per channel independently', () => {
    const { client, socket } = connectClient();
    const onFrame = vi.fn();
    client.onFrame(onFrame);
    client.subscribe('a');
    client.subscribe('b');
    socket.message(dataFrame(5, 'a'));
    socket.message(dataFrame(1, 'b')); // seq 1 on channel b is fresh, not a duplicate
    expect(onFrame).toHaveBeenCalledTimes(2);
  });

  it('ignores malformed JSON without crashing or dispatching', () => {
    const { client, socket } = connectClient();
    const onFrame = vi.fn();
    client.onFrame(onFrame);
    expect(() => socket.raw('{not json')).not.toThrow();
    expect(onFrame).not.toHaveBeenCalled();
  });

  it('ignores non-string message payloads', () => {
    const { client, socket } = connectClient();
    const onFrame = vi.fn();
    client.onFrame(onFrame);
    socket.raw(42);
    expect(onFrame).not.toHaveBeenCalled();
  });

  it('ignores well-formed JSON that is neither data nor control frame', () => {
    const { client, socket } = connectClient();
    const onFrame = vi.fn();
    client.onFrame(onFrame);
    socket.message({ hello: 'world' });
    expect(onFrame).not.toHaveBeenCalled();
  });
});

describe('control frames', () => {
  it('does not dispatch subscribed ack as a data frame', () => {
    const { client, socket } = connectClient();
    const onFrame = vi.fn();
    client.onFrame(onFrame);
    socket.message({ op: 'subscribed', topic: 't' });
    expect(onFrame).not.toHaveBeenCalled();
  });

  it('surfaces error control frames via onError, not onFrame', () => {
    const { client, socket } = connectClient();
    const onError = vi.fn();
    const onFrame = vi.fn();
    client.onError(onError);
    client.onFrame(onFrame);
    socket.message({ op: 'error', code: 'boom', message: 'm' });
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ op: 'error', code: 'boom' }));
    expect(onFrame).not.toHaveBeenCalled();
  });
});

describe('resync_required (§6.7 游标过旧)', () => {
  it('with reconciler: resyncing → reconcile → watermark → resubscribe(no resume) → connected', async () => {
    const reconciler = vi.fn().mockResolvedValue(undefined);
    const onResync = vi.fn();
    const { client, socket } = connectClient({ reconciler });
    client.onResync(onResync);
    client.subscribe('t');
    socket.message(dataFrame(5, 't'));
    socket.message({ op: 'resync_required', topic: 't', watermark: 100, rest: '/api/v1/resync' });

    expect(client.state).toBe('resyncing');
    expect(onResync).toHaveBeenCalledWith({ topic: 't', watermark: 100, rest: '/api/v1/resync' });

    await settle();
    expect(reconciler).toHaveBeenCalledWith({ topic: 't', watermark: 100, rest: '/api/v1/resync' });
    expect(client.state).toBe('connected');

    const ops = socket.sentOps();
    expect(ops[ops.length - 1]).toEqual({ op: 'subscribe', topic: 't' });

    // cursor was reset to watermark: next subscribe resumes from 101
    client.subscribe('t');
    expect(socket.sentOps()).toContainEqual({ op: 'subscribe', topic: 't', resume_from: 101 });
  });

  it('retries with backoff when the reconciler rejects, then recovers', async () => {
    const reconciler = vi.fn().mockRejectedValueOnce(new Error('fail')).mockResolvedValueOnce(undefined);
    const { client, socket, sched } = connectClient({ reconciler, baseDelayMs: 500 });
    client.subscribe('t');
    socket.message({ op: 'resync_required', topic: 't', watermark: 50, rest: '/r' });
    expect(client.state).toBe('resyncing');

    await settle();
    expect(reconciler).toHaveBeenCalledTimes(1);
    expect(client.state).toBe('resyncing');

    const retry = sched.pending.pop();
    expect(retry).toBeDefined();
    expect(retry?.ms).toBeGreaterThanOrEqual(400);
    expect(retry?.ms).toBeLessThanOrEqual(600);
    retry?.fn();
    await settle();

    expect(reconciler).toHaveBeenCalledTimes(2);
    expect(client.state).toBe('connected');
  });

  it('without reconciler: sets watermark, resubscribes, returns to connected synchronously', () => {
    const { client, socket } = connectClient();
    client.subscribe('t');
    socket.message(dataFrame(5, 't'));
    socket.message({ op: 'resync_required', topic: 't', watermark: 999, rest: '/r' });
    expect(client.state).toBe('connected');
    const ops = socket.sentOps();
    expect(ops[ops.length - 1]).toEqual({ op: 'subscribe', topic: 't' });
  });

  it('onResync listener can unsubscribe', () => {
    const reconciler = vi.fn().mockResolvedValue(undefined);
    const { client, socket } = connectClient({ reconciler });
    const onResync = vi.fn();
    const off = client.onResync(onResync);
    off();
    socket.message({ op: 'resync_required', topic: 't', watermark: 1, rest: '/r' });
    expect(onResync).not.toHaveBeenCalled();
  });
});

describe('reconnect with exponential backoff', () => {
  it('close → reconnecting, schedules retry within ±20% of base, resubscribes on reopen', () => {
    const { client, socket, sched } = connectClient({ baseDelayMs: 500, maxDelayMs: 30000 });
    client.subscribe('t');
    socket.emitClose();
    expect(client.state).toBe('reconnecting');

    const retry = sched.pending.pop();
    expect(retry?.ms).toBeGreaterThanOrEqual(400);
    expect(retry?.ms).toBeLessThanOrEqual(600);

    retry?.fn();
    const socket2 = FakeWebSocket.last;
    expect(socket2).not.toBe(socket);
    socket2.open();
    expect(client.state).toBe('connected');
    expect(socket2.sentOps()).toContainEqual({ op: 'subscribe', topic: 't' });
  });

  it('backoff grows on consecutive failures without a successful open', () => {
    const { socket, sched } = connectClient({ baseDelayMs: 500, maxDelayMs: 30000 });
    socket.emitClose();
    const retry0 = sched.pending.pop();
    retry0?.fn(); // opens socket2 but does NOT open it
    const socket2 = FakeWebSocket.last;
    socket2.emitClose();
    const retry1 = sched.pending.pop();
    expect(retry1?.ms).toBeGreaterThanOrEqual(800); // 500*2*0.8
    expect(retry1?.ms).toBeLessThanOrEqual(1200); // 500*2*1.2
  });

  it('delay is capped at maxDelayMs', () => {
    const { sched } = connectClient({ baseDelayMs: 500, maxDelayMs: 2000 });
    // force many consecutive failures; capture the scheduled delay each time
    let lastMs = 0;
    for (let i = 0; i < 10; i++) {
      FakeWebSocket.last.emitClose();
      const retry = sched.pending.pop();
      lastMs = retry?.ms ?? 0;
      retry?.fn();
    }
    expect(lastMs).toBeGreaterThanOrEqual(1600); // 2000*0.8
    expect(lastMs).toBeLessThanOrEqual(2400); // 2000*1.2
  });

  it('attempt counter resets after a successful open', () => {
    const { socket, sched } = connectClient({ baseDelayMs: 500 });
    socket.emitClose();
    sched.pending.pop()?.fn();
    FakeWebSocket.last.open(); // success → reset
    FakeWebSocket.last.emitClose();
    const retry = sched.pending.pop();
    expect(retry?.ms).toBeLessThanOrEqual(600); // back to ~500, not 1000
  });

  it('reconnects on socket error', () => {
    const { client } = connectClient();
    FakeWebSocket.last.emitError();
    expect(client.state).toBe('reconnecting');
  });

  it('error then close schedules exactly one reconnect', () => {
    const { socket, sched } = connectClient();
    const before = sched.pending.length;
    socket.emitError();
    socket.emitClose();
    expect(sched.pending.length).toBe(before + 1);
  });

  it('goes offline if the token disappears before a reconnect attempt', () => {
    let token: string | null = 'tok';
    const { client, socket, sched } = connectClient({ getToken: () => token });
    token = null;
    socket.emitClose();
    sched.pending.pop()?.fn(); // attempt reconnect → no token → offline, no socket
    expect(client.state).toBe('offline');
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it('resubscribes with resume_from reflecting the latest cursor after reconnect', () => {
    const { client, socket, sched } = connectClient();
    client.subscribe('t');
    socket.message(dataFrame(7, 't'));
    socket.emitClose();
    sched.pending.pop()?.fn();
    const socket2 = FakeWebSocket.last;
    socket2.open();
    expect(socket2.sentOps()).toContainEqual({ op: 'subscribe', topic: 't', resume_from: 8 });
  });
});

describe('keepalive ping', () => {
  it('sends ping when idle for pingIntervalMs', () => {
    const { socket, sched, clock } = connectClient({ pingIntervalMs: 100 });
    clock.advance(100);
    sched.pending.pop()?.fn();
    expect(socket.sentOps()).toContainEqual({ op: 'ping' });
  });

  it('does not ping while frames are flowing', () => {
    const { client, socket, sched, clock } = connectClient({ pingIntervalMs: 100 });
    client.subscribe('t');
    clock.advance(50);
    socket.message(dataFrame(1, 't'));
    clock.advance(60); // 60 < 100 since last frame
    sched.pending.pop()?.fn();
    expect(socket.sentOps().map((o) => o.op)).not.toContain('ping');
  });

  it('pong resets the idle timer', () => {
    const { socket, sched, clock } = connectClient({ pingIntervalMs: 100 });
    clock.advance(90);
    socket.message({ op: 'pong' });
    clock.advance(90); // 90 < 100 since pong
    sched.pending.pop()?.fn();
    expect(socket.sentOps().map((o) => o.op)).not.toContain('ping');
  });
});

describe('disconnect', () => {
  it('closes the socket, cancels pending timers, goes idle, no reconnect', () => {
    const { client, socket, sched } = connectClient();
    client.disconnect();
    expect(client.state).toBe('idle');
    expect(socket.closeCalled).toBe(true);
    sched.flush();
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it('ignores late close events from the old socket after disconnect', () => {
    const { client, socket } = connectClient();
    client.disconnect();
    socket.emitClose();
    expect(client.state).toBe('idle');
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it('can reconnect after an explicit disconnect', () => {
    const { client } = connectClient();
    client.disconnect();
    client.connect();
    expect(client.state).toBe('connecting');
    FakeWebSocket.last.open();
    expect(client.state).toBe('connected');
  });
});

describe('listeners', () => {
  it('frame listener unsubscribe stops delivery', () => {
    const { client, socket } = connectClient();
    const onFrame = vi.fn();
    const off = client.onFrame(onFrame);
    off();
    client.subscribe('t');
    socket.message(dataFrame(1, 't'));
    expect(onFrame).not.toHaveBeenCalled();
  });

  it('a throwing frame listener does not break other listeners', () => {
    const { client, socket } = connectClient();
    const bad = vi.fn(() => {
      throw new Error('listener boom');
    });
    const good = vi.fn();
    client.onFrame(bad);
    client.onFrame(good);
    client.subscribe('t');
    socket.message(dataFrame(1, 't'));
    expect(bad).toHaveBeenCalled();
    expect(good).toHaveBeenCalled();
  });

  it('a throwing state listener does not break transitions or other listeners', () => {
    const { client } = makeClient();
    const states: string[] = [];
    client.onState(() => {
      throw new Error('state boom');
    });
    client.onState((s) => states.push(s));
    client.connect();
    FakeWebSocket.last.open();
    expect(states).toContain('connecting');
    expect(states).toContain('connected');
  });

  it('onState listener can unsubscribe', () => {
    const { client } = makeClient();
    const states: string[] = [];
    const off = client.onState((s) => states.push(s));
    client.connect();
    FakeWebSocket.last.open();
    off();
    client.disconnect();
    expect(states).not.toContain('idle');
  });

  it('onError listener can unsubscribe', () => {
    const { client, socket } = connectClient();
    const onError = vi.fn();
    const off = client.onError(onError);
    off();
    socket.message({ op: 'error', code: 'x', message: 'm' });
    expect(onError).not.toHaveBeenCalled();
  });
});

describe('browser online/offline awareness (§6.12 断线体验)', () => {
  it('window offline 事件 → 按断线处理进入 reconnecting(socket 静默挂起也感知)', () => {
    const { client, socket } = connectClient();
    window.dispatchEvent(new Event('offline'));
    expect(client.state).toBe('reconnecting');
    // socket 已被置静默:迟到的 close 不产生二次副作用
    socket.close();
    expect(client.state).toBe('reconnecting');
  });

  it('window online 事件 → 退避清零并立即重连,挂起的退避定时器失效', () => {
    const { client, sched } = connectClient();
    window.dispatchEvent(new Event('offline'));
    expect(client.state).toBe('reconnecting');
    const before = FakeWebSocket.instances.length;
    window.dispatchEvent(new Event('online'));
    expect(client.state).toBe('connecting');
    expect(FakeWebSocket.instances.length).toBe(before + 1);
    // 先前挂起的退避定时器已被 epoch 失效:flush 不产生额外连接
    const after = FakeWebSocket.instances.length;
    sched.flush();
    expect(FakeWebSocket.instances.length).toBe(after);
  });

  it('disconnect 后移除浏览器监听:offline/online 不再触发重连', () => {
    const { client } = connectClient();
    client.disconnect();
    const before = FakeWebSocket.instances.length;
    window.dispatchEvent(new Event('offline'));
    window.dispatchEvent(new Event('online'));
    expect(client.state).toBe('idle');
    expect(FakeWebSocket.instances.length).toBe(before);
  });

  it('connected 状态下 online 事件不产生重复连接', () => {
    const { client } = connectClient();
    const before = FakeWebSocket.instances.length;
    window.dispatchEvent(new Event('online'));
    expect(client.state).toBe('connected');
    expect(FakeWebSocket.instances.length).toBe(before);
  });
});
