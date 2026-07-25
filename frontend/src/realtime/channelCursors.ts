/**
 * 每频道 last_seq 游标(README §6.7 每频道游标 / kanban §2.6)。
 * - 重连带 `resume_from = last_seq + 1`(频道级游标,非视图总游标)
 * - 仅前进:不保存更小/相等的 seq(at-least-once 幂等去重的本地依据)
 * - resync 时经 setWatermark 强制对齐到服务端水位
 * - 持久化于 localStorage `mesh.rt.cursors.v1`(JSON map);存储失败不抛,退回内存
 */

/** 游标持久化键 */
export const CURSORS_STORAGE_KEY = 'mesh.rt.cursors.v1';

type CursorStorage = Pick<Storage, 'getItem' | 'setItem'>;

function defaultStorage(): CursorStorage | undefined {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      return window.localStorage;
    }
  } catch {
    return undefined;
  }
  return undefined;
}

export class ChannelCursors {
  private readonly storage: CursorStorage | undefined;

  private cursors: Record<string, number>;

  constructor(storage?: CursorStorage) {
    this.storage = storage ?? defaultStorage();
    this.cursors = this.load();
  }

  get(channel: string): number | undefined {
    return this.cursors[channel];
  }

  /** 仅前进:绝不保存更小(或相等)的 seq */
  set(channel: string, seq: number): void {
    const current = this.cursors[channel];
    if (current !== undefined && seq <= current) return;
    this.cursors = { ...this.cursors, [channel]: seq };
    this.persist();
  }

  /** 清除单个频道游标(订阅取消时保留,故此处仅供显式清理) */
  clear(channel: string): void {
    if (!(channel in this.cursors)) return;
    const next = { ...this.cursors };
    delete next[channel];
    this.cursors = next;
    this.persist();
  }

  /** resync:强制将游标对齐到服务端水位(即使更低) */
  setWatermark(channel: string, watermark: number): void {
    this.cursors = { ...this.cursors, [channel]: watermark };
    this.persist();
  }

  /** 返回全部游标的快照副本 */
  all(): Record<string, number> {
    return { ...this.cursors };
  }

  private load(): Record<string, number> {
    if (!this.storage) return {};
    try {
      const raw = this.storage.getItem(CURSORS_STORAGE_KEY);
      if (!raw) return {};
      return parseCursors(raw);
    } catch {
      return {};
    }
  }

  private persist(): void {
    if (!this.storage) return;
    try {
      this.storage.setItem(CURSORS_STORAGE_KEY, JSON.stringify(this.cursors));
    } catch {
      // 存储失败不抛:内存态已持有最新游标,持久化尽力而为
    }
  }
}

function parseCursors(raw: string): Record<string, number> {
  const parsed: unknown = JSON.parse(raw);
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return {};
  const result: Record<string, number> = {};
  for (const [channel, value] of Object.entries(parsed as Record<string, unknown>)) {
    if (typeof value === 'number') result[channel] = value;
  }
  return result;
}
