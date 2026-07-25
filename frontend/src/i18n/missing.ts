/**
 * 开发期缺 key 上报 — i18n.md §4.5。
 *
 * - 命中回退链(en / key)时上报 {locale, key, fallback},聚合生成"待翻译清单";
 * - 同 (locale,key) 在窗口内(默认 60s)去重合并,防写放大与滥用;
 * - 批量经可注入的 flush 提交;默认实现 POST /api/v1/i18n/missing,
 *   失败一律静默吞掉 —— 上报绝不影响功能可用性;
 * - enabled 缺省跟随 import.meta.env.DEV(生产关闭,测试可注入)。
 */

export interface MissingEntry {
  readonly locale: string;
  readonly key: string;
  readonly fallback: string;
}

export interface MissingReporter {
  report(locale: string, key: string, fallback: 'en' | 'key'): void;
  readonly reported: ReadonlyArray<MissingEntry>;
}

export interface MissingReporterOptions {
  /** 是否启用;缺省 import.meta.env.DEV(开发期可见,生产关闭) */
  readonly enabled?: boolean;
  /** 同 (locale,key) 去重窗口,默认 60s */
  readonly windowMs?: number;
  /** 批量提交回调;缺省 POST /api/v1/i18n/missing(失败静默) */
  readonly flush?: (batch: ReadonlyArray<MissingEntry>) => void;
}

export const MISSING_REPORT_PATH = '/api/v1/i18n/missing';

const DEFAULT_WINDOW_MS = 60_000;
const FLUSH_DELAY_MS = 0;
/** 去重键分隔符(NUL,不可能出现在 locale/key 文案中) */
const DEDUPE_KEY_SEPARATOR = String.fromCharCode(0);

/**
 * 创建缺 key 上报器。
 * 去重 + 批量提交;report 与 flush 的任何失败都被静默吞掉,绝不向调用方抛出。
 */
export function createMissingReporter(opts?: MissingReporterOptions): MissingReporter {
  const enabled = opts?.enabled ?? Boolean(import.meta.env.DEV);
  const windowMs = opts?.windowMs ?? DEFAULT_WINDOW_MS;
  const flush = opts?.flush ?? defaultFlush;

  const accepted: MissingEntry[] = [];
  const lastSeenAt = new Map<string, number>();
  let pending: MissingEntry[] = [];
  let scheduled: ReturnType<typeof setTimeout> | null = null;

  function scheduleFlush(): void {
    if (scheduled !== null) return;
    scheduled = setTimeout(() => {
      scheduled = null;
      const batch = pending;
      pending = [];
      if (batch.length === 0) return;
      try {
        flush(batch);
      } catch {
        /* 上报失败静默(§4.5:不影响功能可用性) */
      }
    }, FLUSH_DELAY_MS);
  }

  return {
    get reported(): ReadonlyArray<MissingEntry> {
      return [...accepted];
    },
    report(locale: string, key: string, fallback: 'en' | 'key'): void {
      if (!enabled) return;
      const dedupeKey = `${locale}${DEDUPE_KEY_SEPARATOR}${key}`;
      const now = Date.now();
      const last = lastSeenAt.get(dedupeKey);
      if (last !== undefined && now - last < windowMs) return;
      lastSeenAt.set(dedupeKey, now);
      const entry: MissingEntry = { locale, key, fallback };
      accepted.push(entry);
      pending = [...pending, entry];
      scheduleFlush();
    },
  };
}

/**
 * 默认 flush:POST /api/v1/i18n/missing(§3.1 开发期端点)。
 * global fetch 不存在 / 同步抛错 / Promise 拒绝,一律静默。
 */
function defaultFlush(batch: ReadonlyArray<MissingEntry>): void {
  const fetchImpl = globalThis.fetch;
  if (typeof fetchImpl !== 'function') return;
  try {
    const result = fetchImpl(MISSING_REPORT_PATH, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ data: [...batch] }),
    });
    void Promise.resolve(result).catch(() => undefined);
  } catch {
    /* 上报失败静默 */
  }
}
