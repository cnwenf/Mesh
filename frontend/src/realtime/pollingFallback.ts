/**
 * 离线降级轮询(kanban §3.5 降级:WS 断开 → since= 增量拉取)。
 * - 与 RealtimeClient 同形接口(subscribe/unsubscribe/onFrame/onState)
 * - 每次轮询传 `since = 该频道已见最大 updated_at`;结果合成 RealtimeFrame 形状
 * - seq 按频道递增;type 默认 'poll.sync'(调用方可传 eventType);ts 取 item.updated_at,缺省用 now
 * - 出错不停,经 onError 上报,下一拍重试
 */
import type { RealtimeFrame } from '../types/realtime';

export interface PollingSource<T> {
  fetch: (topic: string, since: string | undefined) => Promise<{ items: T[] }>;
}

/** 轮询即「降级的 connected」;未启动为 offline */
export type PollingState = 'offline' | 'connected';

export interface PollingFallbackOptions<T> {
  source: PollingSource<T>;
  /** 轮询间隔,默认 30000ms */
  intervalMs?: number;
  /** 合成帧的事件名,默认 'poll.sync' */
  eventType?: string;
  schedule?: (fn: () => void, ms: number) => void;
  now?: () => number;
}

const DEFAULT_INTERVAL_MS = 30_000;
const DEFAULT_EVENT_TYPE = 'poll.sync';

type FrameListener = (frame: RealtimeFrame) => void;
type StateListener = (state: PollingState) => void;
type ErrorListener = (err: unknown) => void;

export class PollingFallback<T extends { updated_at?: string }> {
  private readonly source: PollingSource<T>;

  private readonly intervalMs: number;

  private readonly eventType: string;

  private readonly schedule: (fn: () => void, ms: number) => void;

  private readonly now: () => number;

  private readonly subscribedTopics = new Set<string>();

  private readonly sinceByTopic = new Map<string, string>();

  private readonly seqByTopic = new Map<string, number>();

  private readonly frameListeners = new Set<FrameListener>();

  private readonly stateListeners = new Set<StateListener>();

  private readonly errorListeners = new Set<ErrorListener>();

  private currentState: PollingState = 'offline';

  private active = false;

  private epoch = 0;

  constructor(opts: PollingFallbackOptions<T>) {
    this.source = opts.source;
    this.intervalMs = opts.intervalMs ?? DEFAULT_INTERVAL_MS;
    this.eventType = opts.eventType ?? DEFAULT_EVENT_TYPE;
    this.schedule =
      opts.schedule ??
      ((fn: () => void, ms: number): void => {
        setTimeout(fn, ms);
      });
    this.now = opts.now ?? ((): number => Date.now());
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

  subscribe(topic: string): void {
    this.subscribedTopics.add(topic);
  }

  unsubscribe(topic: string): void {
    this.subscribedTopics.delete(topic);
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
    for (const topic of [...this.subscribedTopics]) {
      if (!this.active) return;
      const since = this.sinceByTopic.get(topic);
      try {
        const { items } = await this.source.fetch(topic, since);
        this.emitItems(topic, items);
      } catch (err) {
        this.dispatch(this.errorListeners, err); // 保持 started,下一拍重试
      }
    }
  }

  private emitItems(topic: string, items: T[]): void {
    let seq = this.seqByTopic.get(topic) ?? 0;
    let since = this.sinceByTopic.get(topic);
    for (const item of items) {
      seq += 1;
      const ts = item.updated_at ?? new Date(this.now()).toISOString();
      if (item.updated_at && (!since || item.updated_at > since)) {
        since = item.updated_at;
      }
      const frame: RealtimeFrame = {
        seq,
        type: this.eventType,
        topic,
        ts,
        data: item as Record<string, unknown>,
      };
      this.dispatch(this.frameListeners, frame);
    }
    this.seqByTopic.set(topic, seq);
    if (since !== undefined) this.sinceByTopic.set(topic, since);
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
