/**
 * 开发期缺 key 上报测试 — i18n.md §4.5(去重窗口 + 批量上报 + 失败静默,不影响功能)。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MISSING_REPORT_PATH, createMissingReporter } from '../missing';
import type { MissingEntry } from '../missing';

interface ImportMetaWithDev {
  DEV: boolean;
}

describe('createMissingReporter(§4.5)', () => {
  let originalDev: boolean;

  beforeEach(() => {
    originalDev = import.meta.env.DEV;
    vi.useFakeTimers();
  });

  afterEach(() => {
    (import.meta.env as unknown as ImportMetaWithDev).DEV = originalDev;
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('enabled=false 时完全静默(生产可关闭)', () => {
    const flush = vi.fn();
    const reporter = createMissingReporter({ enabled: false, flush });
    reporter.report('zh-CN', 'a.b', 'key');
    vi.runAllTimers();
    expect(reporter.reported).toHaveLength(0);
    expect(flush).not.toHaveBeenCalled();
  });

  it('enabled 缺省跟随 import.meta.env.DEV(开发期默认开启)', () => {
    (import.meta.env as unknown as ImportMetaWithDev).DEV = true;
    const reporter = createMissingReporter({ flush: vi.fn() });
    reporter.report('zh-CN', 'a.b', 'key');
    expect(reporter.reported).toHaveLength(1);

    (import.meta.env as unknown as ImportMetaWithDev).DEV = false;
    const prodReporter = createMissingReporter({ flush: vi.fn() });
    prodReporter.report('zh-CN', 'a.b', 'key');
    expect(prodReporter.reported).toHaveLength(0);
  });

  it('同 (locale,key) 在 60s 窗口内去重合并', () => {
    const reporter = createMissingReporter({ enabled: true, flush: vi.fn() });
    reporter.report('zh-CN', 'issue.create.title', 'key');
    reporter.report('zh-CN', 'issue.create.title', 'en');
    expect(reporter.reported).toHaveLength(1);
  });

  it('不同 key / 不同 locale 各自计数', () => {
    const reporter = createMissingReporter({ enabled: true, flush: vi.fn() });
    reporter.report('zh-CN', 'a', 'key');
    reporter.report('zh-CN', 'b', 'key');
    reporter.report('en', 'a', 'key');
    expect(reporter.reported).toHaveLength(3);
  });

  it('窗口过期后同一 (locale,key) 可再次上报', () => {
    const reporter = createMissingReporter({ enabled: true, flush: vi.fn() });
    reporter.report('zh-CN', 'a', 'key');
    vi.advanceTimersByTime(60_001);
    reporter.report('zh-CN', 'a', 'key');
    expect(reporter.reported).toHaveLength(2);
  });

  it('自定义窗口宽度生效', () => {
    const reporter = createMissingReporter({ enabled: true, windowMs: 1_000, flush: vi.fn() });
    reporter.report('zh-CN', 'a', 'key');
    vi.advanceTimersByTime(1_001);
    reporter.report('zh-CN', 'a', 'key');
    expect(reporter.reported).toHaveLength(2);
  });

  it('批量 flush:窗口内的命中合并为一批,flush 收到完整批次', () => {
    const batches: ReadonlyArray<MissingEntry>[] = [];
    const reporter = createMissingReporter({
      enabled: true,
      flush: (batch) => {
        batches.push(batch);
      },
    });
    reporter.report('zh-CN', 'a', 'key');
    reporter.report('zh-CN', 'b', 'en');
    expect(batches).toHaveLength(0);
    vi.runAllTimers();
    expect(batches).toHaveLength(1);
    expect(batches[0]).toEqual([
      { locale: 'zh-CN', key: 'a', fallback: 'key' },
      { locale: 'zh-CN', key: 'b', fallback: 'en' },
    ]);
  });

  it('flush 抛错时 report 绝不抛出(防失控)', () => {
    const reporter = createMissingReporter({
      enabled: true,
      flush: () => {
        throw new Error('flush boom');
      },
    });
    expect(() => {
      reporter.report('zh-CN', 'a', 'key');
      vi.runAllTimers();
    }).not.toThrow();
    expect(reporter.reported).toHaveLength(1);
  });

  it('reported 返回防御性拷贝,外部修改不影响内部台账', () => {
    const reporter = createMissingReporter({ enabled: true, flush: vi.fn() });
    reporter.report('zh-CN', 'a', 'key');
    const copy = reporter.reported as MissingEntry[];
    copy.push({ locale: 'x', key: 'y', fallback: 'key' });
    expect(reporter.reported).toHaveLength(1);
  });

  it('默认 flush 经 global fetch POST /api/v1/i18n/missing', () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 202 }));
    vi.stubGlobal('fetch', fetchMock);
    const reporter = createMissingReporter({ enabled: true });
    reporter.report('zh-CN', 'demo.missing', 'key');
    vi.runAllTimers();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(MISSING_REPORT_PATH);
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({
      data: [{ locale: 'zh-CN', key: 'demo.missing', fallback: 'key' }],
    });
  });

  it('默认 flush 静默吞掉网络失败(含 fetch 抛错)', () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    const reporter = createMissingReporter({ enabled: true });
    expect(() => {
      reporter.report('zh-CN', 'a', 'key');
      vi.runAllTimers();
    }).not.toThrow();

    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() => {
        throw new Error('sync boom');
      }),
    );
    const reporter2 = createMissingReporter({ enabled: true });
    expect(() => {
      reporter2.report('zh-CN', 'b', 'key');
      vi.runAllTimers();
    }).not.toThrow();
  });

  it('global fetch 不存在时默认 flush 安全跳过', () => {
    vi.stubGlobal('fetch', undefined);
    const reporter = createMissingReporter({ enabled: true });
    expect(() => {
      reporter.report('zh-CN', 'a', 'key');
      vi.runAllTimers();
    }).not.toThrow();
  });
});
