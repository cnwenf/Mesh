/**
 * 实时 WebSocket 客户端(README §6.7 / §6.16,kanban §3.5)。
 * - 子协议鉴权:`new WebSocket(url, [AUTH_SUBPROTOCOL, token])`;token 绝不进 URL query(§6.16)
 * - 每频道 last_seq 游标;订阅带 resume_from=last_seq+1;seq≤游标的重复帧幂等丢弃
 * - resync_required → resyncing 态 + onResync;reconciler 对账成功 → 对齐水位重订阅恢复;失败退避重试
 * - 断线 → reconnecting,指数退避(base×2^n,上限,±20% 抖动)无界重连,重连后按游标重订阅
 * - keepalive:连接态下 pingIntervalMs 无入站帧则发 ping
 * - disconnect() 为主动断开 → idle,不自动重连,取消挂起定时器
 */
import { AUTH_SUBPROTOCOL, isControlFrame, isDataFrame } from '../types/realtime';
import type { ClientOp, RealtimeFrame, ServerControlFrame, SubscribeOp } from '../types/realtime';
import { ChannelCursors } from './channelCursors';

export type ConnectionState =
  | 'idle'
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'resyncing'
  | 'offline';

export interface ResyncRequest {
  topic: string;
  watermark: number;
  rest: string;
}

export interface RealtimeClientOptions {
  /** 例如 ws://host/ws —— 绝不向其追加 token */
  url: string;
  getToken: () => string | null;
  /** 测试注入 FakeWebSocket */
  WebSocketImpl?: typeof WebSocket;
  /** resync REST 对账;成功后客户端重置游标到 watermark 并重订阅 */
  reconciler?: (req: ResyncRequest) => Promise<void>;
  /** 可注入时钟(测试用) */
  now?: () => number;
  /** 可注入定时器(测试用);默认 setTimeout */
  schedule?: (fn: () => void, ms: number) => void;
  baseDelayMs?: number;
  maxDelayMs?: number;
  pingIntervalMs?: number;
}

const DEFAULT_BASE_DELAY_MS = 500;
const DEFAULT_MAX_DELAY_MS = 30_000;
const DEFAULT_PING_INTERVAL_MS = 30_000;
const JITTER_RATIO = 0.2;
const WS_OPEN = 1;

type FrameListener = (frame: RealtimeFrame) => void;
type StateListener = (state: ConnectionState) => void;
type ResyncListener = (req: ResyncRequest) => void;
type ErrorListener = (frame: ServerControlFrame) => void;

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

  private readonly cursors = new ChannelCursors();

  private readonly subscribedTopics = new Set<string>();

  private readonly frameListeners = new Set<FrameListener>();

  private readonly stateListeners = new Set<StateListener>();

  private readonly resyncListeners = new Set<ResyncListener>();

  private readonly errorListeners = new Set<ErrorListener>();

  private socket: WebSocket | null = null;

  private currentState: ConnectionState = 'idle';

  private active = false;

  private intentionalClose = false;

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
    this.timerEpoch += 1;
    this.keepaliveEpoch += 1;
    this.detachBrowserWatch();
    if (this.socket) {
      this.socket.onopen = null;
      this.socket.onmessage = null;
      this.socket.onclose = null;
      this.socket.onerror = null;
      this.socket.close();
      this.socket = null;
    }
    this.setState('idle');
  }

  subscribe(topic: string): void {
    this.subscribedTopics.add(topic);
    if (this.currentState === 'connected') this.sendSubscribe(topic, true);
  }

  unsubscribe(topic: string): void {
    this.subscribedTopics.delete(topic);
    if (this.currentState === 'connected') this.sendOp({ op: 'unsubscribe', topic });
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
    // 子协议鉴权(§6.16):url 原样传入,绝不追加 query 参数;token 经子协议传递
    const socket = new this.WebSocketImpl(this.url, [AUTH_SUBPROTOCOL, token]);
    this.socket = socket;
    socket.onopen = (): void => {
      this.handleOpen();
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

  private handleOpen(): void {
    this.reconnectAttempts = 0;
    this.resyncAttempts = 0;
    this.reconnectPending = false;
    this.lastInboundAt = this.now();
    this.setState('connected');
    for (const topic of this.subscribedTopics) this.sendSubscribe(topic, true);
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
    this.lastInboundAt = this.now();
    if (isDataFrame(parsed)) {
      this.handleDataFrame(parsed);
      return;
    }
    if (isControlFrame(parsed)) this.handleControlFrame(parsed);
  }

  private handleDataFrame(frame: RealtimeFrame): void {
    const cursor = this.cursors.get(frame.topic);
    if (cursor !== undefined && frame.seq <= cursor) return; // at-least-once 幂等去重
    this.cursors.set(frame.topic, frame.seq);
    this.dispatch(this.frameListeners, frame);
  }

  private handleControlFrame(frame: ServerControlFrame): void {
    switch (frame.op) {
      case 'subscribed':
      case 'pong':
        // ack / keepalive 应答:lastInboundAt 已在 handleMessage 更新
        break;
      case 'resync_required':
        this.handleResync({ topic: frame.topic, watermark: frame.watermark, rest: frame.rest });
        break;
      case 'error':
        this.dispatch(this.errorListeners, frame);
        break;
    }
  }

  private handleResync(req: ResyncRequest): void {
    this.setState('resyncing');
    this.dispatch(this.resyncListeners, req);
    const reconciler = this.reconciler;
    if (!reconciler) {
      this.completeResync(req);
      return;
    }
    this.resyncAttempts = 0;
    void this.runReconciler(req, reconciler);
  }

  private runReconciler(
    req: ResyncRequest,
    reconciler: (r: ResyncRequest) => Promise<void>,
  ): Promise<void> {
    return reconciler(req)
      .then(() => {
        this.resyncAttempts = 0;
        this.completeResync(req);
      })
      .catch(() => {
        const delay = this.backoffDelay(this.resyncAttempts);
        this.resyncAttempts += 1;
        this.scheduleGuarded(() => {
          void this.runReconciler(req, reconciler);
        }, delay);
      });
  }

  private completeResync(req: ResyncRequest): void {
    this.cursors.setWatermark(req.topic, req.watermark);
    this.sendSubscribe(req.topic, false); // 重订阅,不带 resume_from(整拉对账后无感恢复)
    this.setState('connected');
  }

  private handleDisconnect(): void {
    if (this.intentionalClose || !this.active) return;
    if (this.reconnectPending) return; // error+close 只排一次重连
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
    if (this.socket) {
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
      if (this.currentState === 'connected' && this.now() - this.lastInboundAt >= this.pingIntervalMs) {
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

  private sendSubscribe(topic: string, withResume: boolean): void {
    const op: SubscribeOp = { op: 'subscribe', topic };
    if (withResume) {
      const cursor = this.cursors.get(topic);
      if (cursor !== undefined) op.resume_from = cursor + 1;
    }
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
