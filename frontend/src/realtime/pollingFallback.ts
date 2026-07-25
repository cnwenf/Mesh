/**
 * 离线降级轮询(§3.2 离线降级轮询机制 / kanban §3.5:WS 断开 → 增量拉取)。
 * - 与 RealtimeClient 同形接口(subscribe/unsubscribe/onFrame/onState/onError)
 * - 数据源为后端对账端点同形的频道事件拉取:`GET /api/v1/realtime/events?channel=&since=<seq>`
 *   (§6.7),返回 seq 大于水位的事件帧;轮询按频道维护 seq 水位,派发真实帧
 *   (与 WS 帧同路径合并,游标守卫天然去重)
 * - 可用 `seedSince(channel, seq)` 以 WS 客户端游标初始化水位,避免重复拉取
 * - 出错不停,经 onError 上报,下一拍重试
 */
import type { RealtimeEventFrame } from '../types/realtime';

export interface PollingSource {
  /** 拉取频道内 seq > since 的事件帧(按 seq 升序) */
  fetch: (channel: string, since: number) => Promise<{ frames: RealtimeEventFrame[] }>;
}

/** 轮询即「降级的 connected」;未启动为 offline */
export type PollingState = 'offline' | 'connected';

export interface PollingFallbackOptions {
  source: PollingSource;
  /** 轮询间隔,默认 30000ms(kanban §3.5) */
  intervalMs?: number;
  schedule?: (fn: () => void, ms: number) => void;
}

const DEFAULT_INTERVAL_MS = 30_000;

type FrameListener = (frame: RealtimeEventFrame) => void;
type StateListener = (state: PollingState) => void;
type ErrorListener = (err: unknown) => void;

export class PollingFallback {
  private readonly source: PollingSource;

  private readonly intervalMs: number;

  private readonly schedule: (fn: () => void, ms: number) => void;

  private readonly subscribedChannels = new Set<string>();

  private readonly sinceByChannel = new Map<string, number>();

  private readonly frameListeners = new Set<FrameListener>();

  private readonly stateListeners = new Set<StateListener>();

  private readonly errorListeners = new Set<ErrorListener>();

  private currentState: PollingState = 'offline';

  private active = false;

  private epoch = 0;

  constructor(opts: PollingFallbackOptions) {
    this.source = opts.source;
    this.intervalMs = opts.intervalMs ?? DEFAULT_INTERVAL_MS;
    this.schedule =
      opts.schedule ??
      ((fn: () => void, ms: number): void => {
        setTimeout(fn, ms);
      });
  }

  get state(): PollingState {
    return this.currentState;
  }

  start(): void {
    if (this.active) return;
    this.active = true;
    this.setState('connected');
    this.scheduleNext();
  }

  stop(): void {
    this.active = false;
    this.epoch += 1;
    this.setState('offline');
  }

  subscribe(channel: string): void {
    this.subscribedChannels.add(channel);
  }

  unsubscribe(channel: string): void {
    this.subscribedChannels.delete(channel);
  }

  /** 以 WS 客户端游标初始化频道水位(仅前进),避免重复拉取已见事件 */
  seedSince(channel: string, seq: number): void {
    const current = this.sinceByChannel.get(channel) ?? 0;
    if (seq > current) this.sinceByChannel.set(channel, seq);
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

  onError(cb: ErrorListener): () => void {
    this.errorListeners.add(cb);
    return (): void => {
      this.errorListeners.delete(cb);
    };
  }

  private scheduleNext(): void {
    const epoch = this.epoch;
    this.schedule(() => {
      if (epoch !== this.epoch) return; // stop() 后失效
      void this.tick();
    }, this.intervalMs);
  }

  private async tick(): Promise<void> {
    if (!this.active) return;
    await this.pollRound();
    this.scheduleNext();
  }

  private async pollRound(): Promise<void> {
    for (const channel of [...this.subscribedChannels]) {
      if (!this.active) return;
      const since = this.sinceByChannel.get(channel) ?? 0;
      try {
        const { frames } = await this.source.fetch(channel, since);
        for (const frame of frames) {
          this.dispatch(this.frameListeners, frame);
          if (frame.seq > (this.sinceByChannel.get(channel) ?? 0)) {
            this.sinceByChannel.set(channel, frame.seq);
          }
        }
      } catch (err) {
        this.dispatch(this.errorListeners, err); // 保持 started,下一拍重试
      }
    }
  }

  private setState(next: PollingState): void {
    if (this.currentState === next) return;
    this.currentState = next;
    this.dispatch(this.stateListeners, next);
  }

  private dispatch<A>(listeners: Set<(arg: A) => void>, arg: A): void {
    for (const listener of [...listeners]) {
      try {
        listener(arg);
      } catch {
        // 单个监听器抛错不得影响其他监听器
      }
    }
  }
}
