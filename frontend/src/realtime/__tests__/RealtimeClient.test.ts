import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RealtimeClient } from '../RealtimeClient';
import type { RealtimeEventFrame } from '../../types/realtime';
import type { RealtimeClientOptions } from '../RealtimeClient';
import { FakeWebSocket, FakeWebSocketImpl } from './FakeWebSocket';

const URL = 'ws://host/ws';
const TOKEN = 'mesh-dev:00000000-0000-0000-0000-000000000001';

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
    getToken: () => TOKEN,
    WebSocketImpl: FakeWebSocketImpl,
    schedule: sched.schedule,
    now: clock.now,
    ...overrides,
  });
  liveClients.push(client);
  return { client, sched, clock };
}

/** construct + connect + open + 完成首帧鉴权(auth_ok),返回已认证连接 */
function connectClient(overrides: Partial<RealtimeClientOptions> = {}) {
  const { client, sched, clock } = makeClient(overrides);
  client.connect();
  const socket = FakeWebSocket.last;
  socket.open();
  socket.message({ op: 'auth_ok' });
  return { client, socket, sched, clock };
}

function eventFrame(
  seq: number,
  channel: string,
  payload: Record<string, unknown> = { id: 'x' },
): RealtimeEventFrame {
  return { op: 'event', channel, seq, event: 'issue.updated', payload };
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

describe('首帧鉴权(§6.16,对齐后端 v0.1.0)', () => {
  it('连接不携带子协议;open 后首帧发送 {op:auth,token},token 绝不进 URL', () => {
    const { client } = makeClient();
    client.connect();
    const socket = FakeWebSocket.last;
    expect(socket.url).toBe(URL);
    expect(socket.url).not.toContain('?');
    expect(socket.url.toLowerCase()).not.toContain('token');
    expect(socket.protocols).toEqual([]);
    socket.open();
    expect(socket.sentOps()).toEqual([{ op: 'auth', token: TOKEN }]);
  });

  it('auth_ok 前为 connecting;auth_ok 后转 connected 并冲刷待发订阅', () => {
    const { client } = makeClient();
    client.subscribe('issue:1');
    client.connect();
    const socket = FakeWebSocket.last;
    socket.open();
    expect(client.state).toBe('connecting');
    expect(socket.sentOps().filter((op) => op.op === 'subscribe')).toHaveLength(0);
    socket.message({ op: 'auth_ok' });
    expect(client.state).toBe('connected');
    expect(socket.sentOps()).toContainEqual({ op: 'subscribe', channel: 'issue:1' });
  });

  it('无 token 时不建连,状态 offline', () => {
    const { client } = makeClient({ getToken: () => null });
    client.connect();
    expect(client.state).toBe('offline');
    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it('鉴权超时(默认 10s)按断线处理,进入退避重连', () => {
    const { client, sched } = makeClient();
    client.connect();
    const socket = FakeWebSocket.last;
    socket.open(); // 发送 auth 帧,但未回 auth_ok
    // 首个定时器即鉴权超时
    const authTimer = sched.pending.at(-1);
    expect(authTimer?.ms).toBe(10_000);
    authTimer?.fn();
    expect(client.state).toBe('reconnecting');
    expect(socket.closeCalled).toBe(true);
  });

  it('鉴权前收到 error 帧 → 派发错误并拆除连接走重连', () => {
    const { client } = makeClient();
    const onError = vi.fn();
    client.onFrame(() => undefined);
    client.onError(onError);
    client.connect();
    const socket = FakeWebSocket.last;
    socket.open();
    socket.message({ op: 'error', code: 'unauthorized', message: 'invalid or expired token' });
    expect(onError).toHaveBeenCalledWith(
      expect.objectContaining({ op: 'error', code: 'unauthorized' }),
    );
    expect(socket.closeCalled).toBe(true);
    expect(client.state).toBe('reconnecting');
  });

  it('auth_ok 后再次收到 auth_ok 不产生副作用(幂等)', () => {
    const { client, socket } = connectClient();
    const states: string[] = [];
    client.onState((s) => states.push(s));
    socket.message({ op: 'auth_ok' });
    expect(client.state).toBe('connected');
    expect(states).toEqual([]); // setState 对相同状态不分发
  });
});

describe('connection lifecycle', () => {
  it('starts idle, transitions connecting → connected via auth handshake', () => {
    const { client } = makeClient();
    expect(client.state).toBe('idle');
    client.connect();
    expect(client.state).toBe('connecting');
    FakeWebSocket.last.open();
    expect(client.state).toBe('connecting'); // 需 auth_ok
    FakeWebSocket.last.message({ op: 'auth_ok' });
    expect(client.state).toBe('connected');
  });

  it('does not open a second socket when connect() is called twice', () => {
    connectClient();
    const { client } = { client: liveClients.at(-1) as RealtimeClient };
    client.connect();
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it('works with default schedule and now (real timers)', () => {
    const client = new RealtimeClient({
      url: URL,
      getToken: () => TOKEN,
      WebSocketImpl: FakeWebSocketImpl,
    });
    liveClients.push(client);
    client.connect();
    const socket = FakeWebSocket.last;
    socket.open();
    socket.message({ op: 'auth_ok' });
    expect(client.state).toBe('connected');
    client.disconnect();
    expect(client.state).toBe('idle');
  });
});

describe('subscribe / unsubscribe', () => {
  it('已认证后订阅发送 {op:subscribe,channel},无游标时不带 resume_from', () => {
    const { client, socket } = connectClient();
    client.subscribe('issue:1');
    expect(socket.sentOps()).toContainEqual({ op: 'subscribe', channel: 'issue:1' });
  });

  it('鉴权前订阅入队,auth_ok 后统一发送', () => {
    const { client } = makeClient();
    client.subscribe('issue:1');
    client.connect();
    const socket = FakeWebSocket.last;
    socket.open();
    socket.message({ op: 'auth_ok' });
    expect(socket.sentOps()).toContainEqual({ op: 'subscribe', channel: 'issue:1' });
  });

  it('有游标时带 resume_from = last_seq + 1', () => {
    const { client, socket } = connectClient();
    client.subscribe('issue:1');
    socket.message(eventFrame(41, 'issue:1'));
    client.subscribe('issue:1');
    expect(socket.sentOps()).toContainEqual({
      op: 'subscribe',
      channel: 'issue:1',
      resume_from: 42,
    });
  });

  it('unsubscribe 发送 {op:unsubscribe,channel}', () => {
    const { client, socket } = connectClient();
    client.subscribe('issue:1');
    client.unsubscribe('issue:1');
    expect(socket.sentOps()).toContainEqual({ op: 'unsubscribe', channel: 'issue:1' });
  });

  it('重连后不重订阅已取消的频道', () => {
    const { client, socket, sched } = connectClient();
    client.subscribe('keep');
    client.subscribe('drop');
    client.unsubscribe('drop');
    socket.emitClose();
    sched.flush(); // 重连退避定时器
    const socket2 = FakeWebSocket.last;
    socket2.open();
    socket2.message({ op: 'auth_ok' });
    const channels = socket2.sentOps().map((op) => op.channel);
    expect(channels).toContain('keep');
    expect(channels).not.toContain('drop');
  });
});

describe('event frames (at-least-once idempotency)', () => {
  it('游标前进并派发 seq 大于游标的帧', () => {
    const { client, socket } = connectClient();
    const onFrame = vi.fn();
    client.onFrame(onFrame);
    client.subscribe('t');
    socket.message(eventFrame(5, 't'));
    expect(onFrame).toHaveBeenCalledTimes(1);
    expect(onFrame).toHaveBeenCalledWith(
      expect.objectContaining({ op: 'event', seq: 5, channel: 't' }),
    );
  });

  it('seq ≤ 游标的重放帧被幂等丢弃', () => {
    const { client, socket } = connectClient();
    const onFrame = vi.fn();
    client.onFrame(onFrame);
    client.subscribe('t');
    socket.message(eventFrame(5, 't'));
    socket.message(eventFrame(5, 't'));
    socket.message(eventFrame(3, 't'));
    expect(onFrame).toHaveBeenCalledTimes(1);
  });

  it('每频道游标独立', () => {
    const { client, socket } = connectClient();
    const onFrame = vi.fn();
    client.onFrame(onFrame);
    client.subscribe('a');
    client.subscribe('b');
    socket.message(eventFrame(5, 'a'));
    socket.message(eventFrame(1, 'b')); // b 频道的 seq 1 不是重复
    expect(onFrame).toHaveBeenCalledTimes(2);
  });

  it('非法 JSON 忽略,不崩溃不派发', () => {
    const { client, socket } = connectClient();
    const onFrame = vi.fn();
    client.onFrame(onFrame);
    socket.raw('not json {');
    socket.raw(42 as unknown as string); // 非字符串数据
    expect(onFrame).not.toHaveBeenCalled();
  });

  it('subscribed{channel,last_seq} 对齐服务端水位(仅前进)', () => {
    const { client, socket } = connectClient();
    const onFrame = vi.fn();
    client.onFrame(onFrame);
    client.subscribe('t');
    socket.message({ op: 'subscribed', channel: 't', last_seq: 10 });
    expect(client.getCursor('t')).toBe(10);
    // last_seq 以下的重放帧被丢弃
    socket.message(eventFrame(10, 't'));
    expect(onFrame).not.toHaveBeenCalled();
    socket.message(eventFrame(11, 't'));
    expect(onFrame).toHaveBeenCalledTimes(1);
    // 更小的 last_seq 不回退游标
    socket.message({ op: 'subscribed', channel: 't', last_seq: 3 });
    expect(client.getCursor('t')).toBe(11);
  });

  it('ingestReconciledEvent 与实时帧同路径(游标守卫 + 派发)', () => {
    const { client } = connectClient();
    const onFrame = vi.fn();
    client.onFrame(onFrame);
    client.ingestReconciledEvent(eventFrame(7, 't', { id: 'r1' }));
    client.ingestReconciledEvent(eventFrame(7, 't', { id: 'r1' })); // 重复
    expect(onFrame).toHaveBeenCalledTimes(1);
    expect(client.getCursor('t')).toBe(7);
  });
});

describe('resync_required (§6.7 游标过旧)', () => {
  it('无 reconciler:对齐水位并以 watermark+1 重订阅,状态回 connected', () => {
    const { client, socket } = connectClient();
    client.subscribe('issue:1');
    socket.message({
      op: 'resync_required',
      channel: 'issue:1',
      watermark: 500,
      rest: '/api/v1/realtime/events?channel=issue%3A1&since=2',
    });
    expect(client.state).toBe('connected');
    expect(client.getCursor('issue:1')).toBe(500);
    expect(socket.sentOps()).toContainEqual({
      op: 'subscribe',
      channel: 'issue:1',
      resume_from: 501,
    });
  });

  it('reconciler 成功后对齐水位重订阅并派发 onResync', async () => {
    const reconciler = vi.fn(async () => undefined);
    const { client, socket } = connectClient({ reconciler });
    const onResync = vi.fn();
    client.onResync(onResync);
    client.subscribe('issue:1');
    socket.message({
      op: 'resync_required',
      channel: 'issue:1',
      watermark: 500,
      rest: '/rest',
    });
    expect(client.state).toBe('resyncing');
    expect(onResync).toHaveBeenCalledWith({ channel: 'issue:1', watermark: 500, rest: '/rest' });
    await settle();
    expect(reconciler).toHaveBeenCalledWith({ channel: 'issue:1', watermark: 500, rest: '/rest' });
    expect(client.state).toBe('connected');
    expect(client.getCursor('issue:1')).toBe(500);
    expect(socket.sentOps()).toContainEqual({
      op: 'subscribe',
      channel: 'issue:1',
      resume_from: 501,
    });
  });

  it('reconciler 失败 → 退避重试(独立计数)', async () => {
    const reconciler = vi.fn(async () => {
      throw new Error('boom');
    });
    const { client, sched } = connectClient({ reconciler });
    client.subscribe('issue:1');
    const socket = FakeWebSocket.last;
    socket.message({ op: 'resync_required', channel: 'issue:1', watermark: 9, rest: '/r' });
    await settle();
    expect(client.state).toBe('resyncing');
    // 重试样例的定时器在队列末尾(毫秒数 > 0)
    const retryTimer = sched.pending.at(-1);
    expect(retryTimer?.ms).toBeGreaterThanOrEqual(400); // base 500 × 0.8 下限
    // 重试仍失败则继续退避
    retryTimer?.fn();
    await settle();
    expect(reconciler).toHaveBeenCalledTimes(2);
    expect(client.state).toBe('resyncing');
  });
});

describe('reconnect with exponential backoff', () => {
  it('断线 → reconnecting,退避后重连并重新鉴权', () => {
    const { client, socket, sched, clock } = connectClient();
    const states: string[] = [];
    client.onState((s) => states.push(s));
    socket.emitClose();
    expect(client.state).toBe('reconnecting');
    const timer = sched.pending.at(-1);
    expect(timer?.ms).toBeGreaterThanOrEqual(400);
    expect(timer?.ms).toBeLessThanOrEqual(600); // base 500 ± 20%
    clock.advance(1000);
    timer?.fn();
    const socket2 = FakeWebSocket.last;
    expect(socket2).not.toBe(socket);
    socket2.open();
    socket2.message({ op: 'auth_ok' });
    expect(client.state).toBe('connected');
    expect(states).toEqual(['reconnecting', 'connected']);
  });

  it('退避指数增长并有上限', () => {
    const { client, socket, sched } = connectClient({ baseDelayMs: 500, maxDelayMs: 4000 });
    const delays: number[] = [];
    for (let i = 0; i < 6; i++) {
      socket.emitClose();
      const timer = sched.pending.at(-1);
      if (timer) delays.push(timer.ms);
      timer?.fn();
      FakeWebSocket.last.open();
      FakeWebSocket.last.message({ op: 'auth_ok' });
      if (client.state !== 'connected') break;
      // 再次断开进入下一轮退避
      FakeWebSocket.last.emitClose();
      const t2 = sched.pending.at(-1);
      if (t2) delays.push(t2.ms);
      t2?.fn();
    }
    for (const d of delays) expect(d).toBeLessThanOrEqual(4800); // 4000 × 1.2 抖动上限
  });

  it('error+close 同拍只排一次重连', () => {
    const { client, socket, sched } = connectClient();
    socket.emitError();
    socket.emitClose();
    const reconnectTimers = sched.pending.filter((t) => t.ms >= 400 && t.ms <= 600);
    expect(reconnectTimers).toHaveLength(1);
    expect(client.state).toBe('reconnecting');
  });

  it('disconnect() 后挂起重连定时器失效', () => {
    const { client, socket, sched } = connectClient();
    socket.emitClose();
    expect(client.state).toBe('reconnecting');
    client.disconnect();
    expect(client.state).toBe('idle');
    const before = FakeWebSocket.instances.length;
    sched.flush();
    expect(FakeWebSocket.instances.length).toBe(before); // 未再建连
  });
});

describe('keepalive ping', () => {
  it('连接态下超过 pingInterval 无入站帧则发 ping', () => {
    const { socket, sched, clock } = connectClient({ pingIntervalMs: 30_000 });
    // connectClient 后首个定时器为 keepalive tick(auth 完成后 arm)
    const tick = sched.pending.at(-1);
    expect(tick?.ms).toBe(30_000);
    clock.advance(31_000);
    tick?.fn();
    expect(socket.sentOps()).toContainEqual({ op: 'ping' });
  });

  it('窗口内有入站帧则不发 ping', () => {
    const { socket, sched, clock } = connectClient({ pingIntervalMs: 30_000 });
    const tick = sched.pending.at(-1);
    clock.advance(10_000);
    socket.message({ op: 'ping' }); // 服务端心跳刷新 lastInboundAt
    clock.advance(21_000); // 总 31s,但距上次入站仅 21s
    tick?.fn();
    expect(socket.sentOps().filter((op) => op.op === 'ping')).toHaveLength(0);
  });

  it('服务端 ping 帧仅刷新入站时间,不派发', () => {
    const { client, socket } = connectClient();
    const onFrame = vi.fn();
    client.onFrame(onFrame);
    socket.message({ op: 'ping' });
    expect(onFrame).not.toHaveBeenCalled();
    expect(client.state).toBe('connected');
  });
});

describe('browser online/offline awareness (§6.12 断线体验)', () => {
  it('window offline 事件 → 按断线处理进入 reconnecting(socket 静默挂起也感知)', () => {
    const { client, socket } = connectClient();
    window.dispatchEvent(new Event('offline'));
    expect(client.state).toBe('reconnecting');
    // socket 已被置静默:迟到的 close 不产生二次副作用
    socket.emitClose();
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

describe('disconnect', () => {
  it('主动断开 → idle,清除 handlers 并关闭 socket', () => {
    const { client, socket } = connectClient();
    client.disconnect();
    expect(client.state).toBe('idle');
    expect(socket.closeCalled).toBe(true);
  });

  it('未连接时 disconnect 安全幂等', () => {
    const { client } = makeClient();
    expect(() => client.disconnect()).not.toThrow();
    expect(client.state).toBe('idle');
  });
});

describe('listeners', () => {
  it('onState/onFrame/onResync/onError 可取消订阅', () => {
    const { client, socket } = connectClient();
    const onFrame = vi.fn();
    const off = client.onFrame(onFrame);
    off();
    socket.message(eventFrame(1, 't'));
    expect(onFrame).not.toHaveBeenCalled();
  });

  it('单个监听器抛错不影响其他监听器', () => {
    const { client, socket } = connectClient();
    const good = vi.fn();
    client.onFrame(() => {
      throw new Error('listener bug');
    });
    client.onFrame(good);
    socket.message(eventFrame(1, 't'));
    expect(good).toHaveBeenCalledTimes(1);
  });

  it('onState 监听器可取消', () => {
    const { client, socket } = connectClient();
    const onState = vi.fn();
    const off = client.onState(onState);
    off();
    socket.emitClose();
    expect(onState).not.toHaveBeenCalled();
  });

  it('onError 监听器可取消', () => {
    const { client } = makeClient();
    const onError = vi.fn();
    const off = client.onError(onError);
    client.connect();
    const socket = FakeWebSocket.last;
    socket.open();
    off();
    socket.message({ op: 'error', code: 'x', message: 'm' });
    expect(onError).not.toHaveBeenCalled();
  });
});
