/**
 * L182 通用乐观操作队列 — README §6.12 异常态矩阵 offline 行「乐观操作排队」。
 *
 * 偏好写有专用持久队列(state/pendingSettingsQueue.ts,theme.md §2.3);
 * 本模块提供**通用内存队列**:断线(navigator.onLine false 或请求 network 错误)
 * 时操作入队,`online` 事件 / realtime 重连(经额外触发器)后按 FIFO 回放,
 * 每项独立标记 queued → running → succeeded(移除)/ failed(保留供展示)。
 *
 * 约束:入队操作必须幂等(PUT/DELETE 语义);非幂等创建不得入队,
 * 由调用方在 submit 前自行判断。
 */
import { MeshApiError } from './errors';

export type OptimisticQueueStatus = 'queued' | 'running' | 'succeeded' | 'failed';

export interface OptimisticQueueItem {
  readonly id: string;
  /** 操作可读描述(呈现层用) */
  readonly label: string;
  readonly status: OptimisticQueueStatus;
  /** 失败原因(仅 failed 态非 null) */
  readonly error: MeshApiError | null;
  /** 已尝试回放次数 */
  readonly attempts: number;
}

export interface OptimisticQueueOptions {
  /** 离线判定(缺省 navigator.onLine;测试可注入) */
  readonly isOffline?: () => boolean;
  /** 单项回放尝试上限,超限标 failed 不再重试(默认 3) */
  readonly maxAttempts?: number;
  /** 队列容量,超限丢弃最旧条目(默认 64) */
  readonly maxQueueSize?: number;
}

export type OptimisticQueueListener = (items: readonly OptimisticQueueItem[]) => void;

/** submit 结果:在线直接执行成功(executed),或已入队待回放(queued) */
export type SubmitOutcome = 'executed' | 'queued';

export interface FlushSummary {
  readonly succeeded: number;
  readonly failed: number;
  /** 回放后仍在队列中的条目数(queued + failed) */
  readonly remaining: number;
}

export interface OptimisticQueue {
  /**
   * 提交操作:在线即执行(成功 'executed';network 错误转入队列 'queued';
   * 其余错误上抛由调用方收敛/回滚);离线直接入队。
   */
  submit(label: string, run: () => Promise<unknown>): Promise<SubmitOutcome>;
  /** 离线或正在回放时为空操作;按 FIFO 回放并逐项标记结果。 */
  flush(): Promise<FlushSummary>;
  items(): readonly OptimisticQueueItem[];
  /** queued + running 计数(呈现层「N 个操作待同步」)。 */
  pendingCount(): number;
  /** 移除指定条目(失败条目确认后清理);不存在即空操作。 */
  remove(id: string): void;
  clear(): void;
  subscribe(listener: OptimisticQueueListener): () => void;
  /** 清空条目与监听;后续 submit/flush 仍可安全调用(空队列语义)。 */
  dispose(): void;
}

interface InternalEntry {
  readonly item: OptimisticQueueItem;
  readonly run: () => Promise<unknown>;
}

const DEFAULT_MAX_ATTEMPTS = 3;
const DEFAULT_MAX_QUEUE_SIZE = 64;

let idCounter = 0;

function nextId(): string {
  idCounter += 1;
  return `opq-${idCounter}`;
}

/** network 类错误判定(断线/请求未达):status 0 或具名 code network。 */
export function isNetworkError(err: unknown): boolean {
  return err instanceof MeshApiError && (err.status === 0 || err.code === 'network');
}

/** 缺省离线判定:浏览器 navigator.onLine;非浏览器环境视为在线。 */
export function defaultIsOffline(): boolean {
  return typeof navigator !== 'undefined' && navigator.onLine === false;
}

export function createOptimisticQueue(options: OptimisticQueueOptions = {}): OptimisticQueue {
  const isOffline = options.isOffline ?? defaultIsOffline;
  const maxAttempts = options.maxAttempts ?? DEFAULT_MAX_ATTEMPTS;
  const maxQueueSize = options.maxQueueSize ?? DEFAULT_MAX_QUEUE_SIZE;

  let entries: InternalEntry[] = [];
  let listeners: OptimisticQueueListener[] = [];
  let flushing = false;

  const snapshot = (): readonly OptimisticQueueItem[] => entries.map((entry) => entry.item);

  const notify = (): void => {
    const items = snapshot();
    for (const listener of [...listeners]) listener(items);
  };

  const markItem = (
    id: string,
    patch: Partial<Pick<OptimisticQueueItem, 'status' | 'error' | 'attempts'>>,
  ): void => {
    entries = entries.map((entry) =>
      entry.item.id === id ? { ...entry, item: { ...entry.item, ...patch } } : entry,
    );
  };

  const enqueue = (label: string, run: () => Promise<unknown>): void => {
    const item: OptimisticQueueItem = {
      id: nextId(),
      label,
      status: 'queued',
      error: null,
      attempts: 0,
    };
    entries = [...entries, { item, run }];
    // 容量护栏:超限丢弃最旧(仍保最新意图)。
    if (entries.length > maxQueueSize) entries = entries.slice(entries.length - maxQueueSize);
    notify();
  };

  const submit = async (label: string, run: () => Promise<unknown>): Promise<SubmitOutcome> => {
    if (isOffline()) {
      enqueue(label, run);
      return 'queued';
    }
    try {
      await run();
      return 'executed';
    } catch (err) {
      if (isNetworkError(err)) {
        enqueue(label, run);
        return 'queued';
      }
      throw err;
    }
  };

  const flush = async (): Promise<FlushSummary> => {
    if (flushing || isOffline()) {
      return { succeeded: 0, failed: 0, remaining: entries.length };
    }
    flushing = true;
    let succeeded = 0;
    let failed = 0;
    try {
      const pending = entries.filter((entry) => entry.item.status === 'queued');
      for (const entry of pending) {
        const { id } = entry.item;
        // 条目可能在回放中被 remove/clear:以当前快照为准。
        if (!entries.some((current) => current.item.id === id)) continue;
        markItem(id, { status: 'running' });
        notify();
        try {
          await entry.run();
          entries = entries.filter((current) => current.item.id !== id);
          succeeded += 1;
        } catch (err) {
          const attempts = entry.item.attempts + 1;
          if (isNetworkError(err) && attempts < maxAttempts) {
            // 网络仍不可达:回到 queued 待下轮(online/重连)再试。
            markItem(id, { status: 'queued', attempts });
          } else {
            markItem(id, {
              status: 'failed',
              attempts,
              error:
                err instanceof MeshApiError
                  ? err
                  : new MeshApiError({ status: 0, code: 'network', message: 'network error' }),
            });
            failed += 1;
          }
        }
      }
    } finally {
      flushing = false;
    }
    notify();
    return {
      succeeded,
      failed,
      remaining: entries.filter((entry) => entry.item.status === 'queued').length,
    };
  };

  const remove = (id: string): void => {
    const before = entries.length;
    entries = entries.filter((entry) => entry.item.id !== id);
    if (entries.length !== before) notify();
  };

  const clear = (): void => {
    if (entries.length === 0) return;
    entries = [];
    notify();
  };

  const subscribe = (listener: OptimisticQueueListener): (() => void) => {
    listeners = [...listeners, listener];
    return () => {
      listeners = listeners.filter((current) => current !== listener);
    };
  };

  const dispose = (): void => {
    entries = [];
    listeners = [];
  };

  return {
    submit,
    flush,
    items: snapshot,
    pendingCount: () =>
      entries.filter((entry) => entry.item.status === 'queued' || entry.item.status === 'running')
        .length,
    remove,
    clear,
    subscribe,
    dispose,
  };
}

/**
 * 注册回放触发器:`online` 事件 + 额外触发源(如 realtime ConnectionState → connected)。
 * 返回拆卸函数(全部解绑)。
 */
export function initOptimisticQueueTriggers(
  queue: OptimisticQueue,
  options: {
    /** 额外触发源订阅器:收到 fire 即回放;返回各自的拆卸函数 */
    readonly extraTriggers?: readonly ((fire: () => void) => () => void)[];
    /** 缺省 true:监听 window online 事件 */
    readonly listenOnline?: boolean;
  } = {},
): () => void {
  const fire = (): void => {
    void queue.flush();
  };
  const teardowns: Array<() => void> = [];
  if (options.listenOnline !== false && typeof window !== 'undefined') {
    window.addEventListener('online', fire);
    teardowns.push(() => window.removeEventListener('online', fire));
  }
  for (const subscribeExtra of options.extraTriggers ?? []) {
    teardowns.push(subscribeExtra(fire));
  }
  return () => {
    for (const teardown of teardowns) teardown();
  };
}
