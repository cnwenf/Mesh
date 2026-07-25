/**
 * 实时 WebSocket 客户端 — 线缆协议与后端 v0.1.0(`backend/src/mesh/realtime/session.py`)
 * 逐帧对齐;契约权威 docs/specs/README.md §6.7 / §6.16,kanban §3.5。
 *
 * - 首帧鉴权(§6.16 首帧认证单一机制;v0.1.0 起实现基线):连接建立后发送
 *   `{op:'auth', token}`,等待 `{op:'auth_ok'}`(默认 10s 超时按断线重连处理);
 *   token 绝不进 URL query。
 * - 每频道 last_seq 游标;订阅带 resume_from=last_seq+1;seq≤游标的重复帧幂等丢弃
 * - `{op:'subscribed', channel, last_seq}` 确认订阅并对齐服务端水位
 * - resync_required → resyncing 态 + onResync;reconciler 对账成功 → 对齐水位、
 *   以 resume_from=watermark+1 重订阅恢复;失败退避重试
 * - 断线 → reconnecting,指数退避(base×2^n,上限,±20% 抖动)无界重连,重连后
 *   重新鉴权并按游标重订阅;浏览器 online/offline 事件主动感知网络层断线
 * - keepalive:连接态下 pingIntervalMs 无入站帧则发 ping(服务端亦主动心跳)
 * - disconnect() 为主动断开 → idle,不自动重连,取消挂起定时器
 */
import { isEventFrame, isServerFrame } from '../types/realtime';
import type {
  ClientOp,
  ErrorFrame,
  RealtimeEventFrame,
  ResyncRequiredFrame,
  SubscribedFrame,
} from '../types/realtime';
import { ChannelCursors } from './channelCursors';

export type ConnectionState =
  | 'idle'
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'resyncing'
  | 'offline';

export interface ResyncRequest {
  channel: string;
  watermark: number;
  rest: string;
}

export interface RealtimeClientOptions {
  /** 例如 ws://host/ws —— 绝不向其追加 token */
  url: string;
  getToken: () => string | null;
  /** 测试注入 FakeWebSocket */
  WebSocketImpl?: typeof WebSocket;
  /** resync REST 对账(拉取 rest 并合并);成功后客户端对齐水位重订阅 */
  reconciler?: (req: ResyncRequest) => Promise<void>;
  /** 可注入时钟(测试用) */
  now?: () => number;
  /** 可注入定时器(测试用);默认 setTimeout */
  schedule?: (fn: () => void, ms: number) => void;
  baseDelayMs?: number;
  maxDelayMs?: number;
  pingIntervalMs?: number;
  /** 首帧鉴权超时(后端为 10s);超时按断线处理 */
  authTimeoutMs?: number;
}

const DEFAULT_BASE_DELAY_MS = 500;
const DEFAULT_MAX_DELAY_MS = 30_000;
const DEFAULT_PING_INTERVAL_MS = 30_000;
const DEFAULT_AUTH_TIMEOUT_MS = 10_000;
const JITTER_RATIO = 0.2;
const WS_OPEN = 1;

type FrameListener = (frame: RealtimeEventFrame) => void;
type StateListener = (state: ConnectionState) => void;
type ResyncListener = (req: ResyncRequest) => void;
type ErrorListener = (frame: ErrorFrame) => void;

export class RealtimeClient {
  private readonly url: string;

  private readonly getToken: () => string | null;

  private readonly WebSocketImpl: typeof WebSocket;

  private readonly reconciler: ((req: ResyncRequest) => Promise<void>) | undefined;

  private readonly now: () => number;

  private readonly schedule: (fn: () => void, ms: number) => void;

  private readonly baseDelayMs: number;

  private readonly maxDelayMs: number;

  private readonly pingIntervalMs: number;

  private readonly authTimeoutMs: number;

  private readonly cursors = new ChannelCursors();

  private readonly subscribedChannels = new Set<string>();

  private readonly frameListeners = new Set<FrameListener>();

  private readonly stateListeners = new Set<StateListener>();

  private readonly resyncListeners = new Set<ResyncListener>();

  private readonly errorListeners = new Set<ErrorListener>();

  private socket: WebSocket | null = null;

  private currentState: ConnectionState = 'idle';

  private active = false;

  private intentionalClose = false;

  private authenticated = false;

  private reconnectPending = false;

  private reconnectAttempts = 0;

  private resyncAttempts = 0;

  private timerEpoch = 0;

  private keepaliveEpoch = 0;

  private lastInboundAt = 0;

  private browserWatchAttached = false;

  private readonly browserOfflineHandler = (): void => {
    this.handleBrowserOffline();
  };

  private readonly browserOnlineHandler = (): void => {
    this.handleBrowserOnline();
  };

  constructor(opts: RealtimeClientOptions) {
    this.url = opts.url;
    this.getToken = opts.getToken;
    this.WebSocketImpl = opts.WebSocketImpl ?? WebSocket;
    this.reconciler = opts.reconciler;
    this.now = opts.now ?? ((): number => Date.now());
    this.schedule =
      opts.schedule ??
      ((fn: () => void, ms: number): void => {
        setTimeout(fn, ms);
      });
    this.baseDelayMs = opts.baseDelayMs ?? DEFAULT_BASE_DELAY_MS;
    this.maxDelayMs = opts.maxDelayMs ?? DEFAULT_MAX_DELAY_MS;
    this.pingIntervalMs = opts.pingIntervalMs ?? DEFAULT_PING_INTERVAL_MS;
    this.authTimeoutMs = opts.authTimeoutMs ?? DEFAULT_AUTH_TIMEOUT_MS;
  }

  get state(): ConnectionState {
    return this.currentState;
  }

  connect(): void {
    if (this.active) return;
    this.active = true;
    this.intentionalClose = false;
    this.attachBrowserWatch();
    if (!this.getToken()) {
      this.setState('offline');
      return;
    }
    this.setState('connecting');
    this.openSocket();
  }

  /** 主动断开:state → idle,不自动重连,取消挂起定时器 */
  disconnect(): void {
    this.active = false;
    this.intentionalClose = true;
    this.reconnectPending = false;
    this.authenticated = false;
    this.timerEpoch += 1;
    this.keepaliveEpoch += 1;
    this.detachBrowserWatch();
    this.teardownSocket();
    this.setState('idle');
  }

  subscribe(channel: string): void {
    this.subscribedChannels.add(channel);
    if (this.authenticated) this.sendSubscribe(channel);
  }

  unsubscribe(channel: string): void {
    this.subscribedChannels.delete(channel);
    if (this.authenticated) this.sendOp({ op: 'unsubscribe', channel });
  }

  /** 当前频道游标(供轮询降级等读取 since 水位) */
  getCursor(channel: string): number | undefined {
    return this.cursors.get(channel);
  }

  /** 注入 REST 对账拉回的事件(与实时帧同路径:游标守卫 + 派发) */
  ingestReconciledEvent(frame: RealtimeEventFrame): void {
    this.handleEventFrame(frame);
  }

  onFrame(cb: FrameListener): () => void {
    this.frameListeners.add(cb);
    return (): void => {
      this.frameListeners.delete(cb);
    };
  }

  onState(cb: StateListener): () => void {
    this.stateListeners.add(cb);
    return (): void => {
      this.stateListeners.delete(cb);
    };
  }

  onResync(cb: ResyncListener): () => void {
    this.resyncListeners.add(cb);
    return (): void => {
      this.resyncListeners.delete(cb);
    };
  }

  onError(cb: ErrorListener): () => void {
    this.errorListeners.add(cb);
    return (): void => {
      this.errorListeners.delete(cb);
    };
  }

  private openSocket(): void {
    const token = this.getToken();
    if (!token) {
      this.setState('offline');
      return;
    }
    this.authenticated = false;
    // §6.16:token 绝不进 URL;首帧认证单一机制(v0.1.0 起实现基线)
    const socket = new this.WebSocketImpl(this.url);
    this.socket = socket;
    socket.onopen = (): void => {
      this.sendOp({ op: 'auth', token });
      this.armAuthTimeout();
    };
    socket.onmessage = (ev: MessageEvent): void => {
      this.handleMessage(ev.data);
    };
    socket.onclose = (): void => {
      this.handleDisconnect();
    };
    socket.onerror = (): void => {
      this.handleDisconnect();
    };
  }

  private teardownSocket(): void {
    if (!this.socket) return;
    this.socket.onopen = null;
    this.socket.onmessage = null;
    this.socket.onclose = null;
    this.socket.onerror = null;
    try {
      this.socket.close();
    } catch {
      /* 关闭失败不影响后续重连 */
    }
    this.socket = null;
  }

  /** 首帧鉴权超时(与服务端 10s 对齐):按断线处理,走退避重连 */
  private armAuthTimeout(): void {
    this.scheduleGuarded(() => {
      if (!this.authenticated) {
        this.teardownSocket();
        this.handleDisconnect();
      }
    }, this.authTimeoutMs);
  }

  private handleAuthOk(): void {
    this.authenticated = true;
    this.reconnectAttempts = 0;
    this.resyncAttempts = 0;
    this.reconnectPending = false;
    this.lastInboundAt = this.now();
    this.setState('connected');
    for (const channel of this.subscribedChannels) this.sendSubscribe(channel);
    this.armKeepalive();
  }

  private handleMessage(raw: unknown): void {
    if (typeof raw !== 'string') return;
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      return; // 非法 JSON:忽略,不崩溃
    }
    if (!isServerFrame(parsed)) return;
    this.lastInboundAt = this.now();
    switch (parsed.op) {
      case 'auth_ok':
        this.handleAuthOk();
        return;
      case 'event':
        if (isEventFrame(parsed)) this.handleEventFrame(parsed);
        return;
      case 'subscribed':
        this.handleSubscribed(parsed);
        return;
      case 'resync_required':
        this.handleResyncRequested(parsed);
        return;
      case 'error':
        this.handleErrorFrame(parsed);
        return;
      case 'ping':
        return; // 服务端心跳:lastInboundAt 已更新
    }
  }

  private handleEventFrame(frame: RealtimeEventFrame): void {
    const cursor = this.cursors.get(frame.channel);
    if (cursor !== undefined && frame.seq <= cursor) return; // at-least-once 幂等去重
    this.cursors.set(frame.channel, frame.seq);
    this.dispatch(this.frameListeners, frame);
  }

  private handleSubscribed(frame: SubscribedFrame): void {
    // 订阅/重放完成确认:对齐服务端频道水位(仅前进)
    this.cursors.set(frame.channel, frame.last_seq);
  }

  private handleErrorFrame(frame: ErrorFrame): void {
    this.dispatch(this.errorListeners, frame);
    if (!this.authenticated) {
      // 鉴权失败:服务端将关闭连接;主动拆除并按断线重连(退避)
      this.teardownSocket();
      this.handleDisconnect();
    }
  }

  private handleResyncRequested(frame: ResyncRequiredFrame): void {
    const req: ResyncRequest = {
      channel: frame.channel,
      watermark: frame.watermark,
      rest: frame.rest,
    };
    this.setState('resyncing');
    this.dispatch(this.resyncListeners, req);
    const complete = (): void => {
      this.cursors.setWatermark(frame.channel, frame.watermark);
      // 服务端已丢弃该订阅(resync 时 discard):以 watermark+1 重新订阅
      this.subscribedChannels.add(frame.channel);
      if (this.authenticated) this.sendSubscribe(frame.channel);
      this.resyncAttempts = 0;
      this.setState('connected');
    };
    if (!this.reconciler) {
      complete();
      return;
    }
    this.reconciler(req).then(complete, () => {
      // 对账失败:退避重试(与重连共享上限,独立计数)
      const delay = this.backoffDelay(this.resyncAttempts);
      this.resyncAttempts += 1;
      this.scheduleGuarded(() => {
        this.handleResyncRequested(frame);
      }, delay);
    });
  }

  private handleDisconnect(): void {
    if (this.intentionalClose || !this.active) return;
    if (this.reconnectPending) return; // error+close 只排一次重连
    this.authenticated = false;
    this.reconnectPending = true;
    this.setState('reconnecting');
    const delay = this.backoffDelay(this.reconnectAttempts);
    this.reconnectAttempts += 1;
    this.scheduleGuarded(() => {
      this.reconnectPending = false;
      this.openSocket();
    }, delay);
  }

  /**
   * 浏览器网络层离线 → 按断线处理。浏览器对「闷死」的 WebSocket 未必及时触发
   * close/error(如系统离线时 TCP 静默挂起),监听 window 'offline' 事件主动感知,
   * 是 §6.12「断线体验」与 §3.2 离线降级的第一触发点。
   */
  private handleBrowserOffline(): void {
    if (!this.active || this.intentionalClose) return;
    this.teardownSocket();
    this.handleDisconnect();
  }

  /** 浏览器恢复在线 → 退避计数清零并立即重连,不等下一个退避窗口 */
  private handleBrowserOnline(): void {
    if (!this.active || this.intentionalClose) return;
    this.reconnectAttempts = 0;
    if (this.currentState === 'connected' || this.currentState === 'connecting') return;
    this.reconnectPending = false;
    this.timerEpoch += 1; // 使挂起的退避定时器失效
    this.setState('connecting');
    this.openSocket();
  }

  private attachBrowserWatch(): void {
    if (this.browserWatchAttached || typeof window === 'undefined') return;
    window.addEventListener('offline', this.browserOfflineHandler);
    window.addEventListener('online', this.browserOnlineHandler);
    this.browserWatchAttached = true;
  }

  private detachBrowserWatch(): void {
    if (!this.browserWatchAttached || typeof window === 'undefined') return;
    window.removeEventListener('offline', this.browserOfflineHandler);
    window.removeEventListener('online', this.browserOnlineHandler);
    this.browserWatchAttached = false;
  }

  private armKeepalive(): void {
    const gen = this.keepaliveEpoch + 1;
    this.keepaliveEpoch = gen;
    const tick = (): void => {
      if (gen !== this.keepaliveEpoch) return;
      if (
        this.authenticated &&
        this.currentState === 'connected' &&
        this.now() - this.lastInboundAt >= this.pingIntervalMs
      ) {
        this.sendOp({ op: 'ping' });
      }
      this.scheduleGuarded(tick, this.pingIntervalMs);
    };
    this.scheduleGuarded(tick, this.pingIntervalMs);
  }

  private scheduleGuarded(fn: () => void, ms: number): void {
    const epoch = this.timerEpoch;
    this.schedule(() => {
      if (epoch !== this.timerEpoch) return; // disconnect 后失效
      fn();
    }, ms);
  }

  private backoffDelay(attempt: number): number {
    const base = Math.min(this.maxDelayMs, this.baseDelayMs * 2 ** attempt);
    const jitter = 1 + (Math.random() * 2 - 1) * JITTER_RATIO; // [0.8, 1.2]
    return Math.round(base * jitter);
  }

  private sendSubscribe(channel: string): void {
    const op: ClientOp = { op: 'subscribe', channel };
    const cursor = this.cursors.get(channel);
    if (cursor !== undefined) (op as { resume_from?: number }).resume_from = cursor + 1;
    this.sendOp(op);
  }

  private sendOp(op: ClientOp): void {
    if (this.socket && this.socket.readyState === WS_OPEN) {
      this.socket.send(JSON.stringify(op));
    }
  }

  private setState(next: ConnectionState): void {
    if (this.currentState === next) return;
    this.currentState = next;
    this.dispatch(this.stateListeners, next);
  }

  private dispatch<T>(listeners: Set<(arg: T) => void>, arg: T): void {
    for (const listener of [...listeners]) {
      try {
        listener(arg);
      } catch {
        // 单个监听器抛错不得影响其他监听器
      }
    }
  }
}
