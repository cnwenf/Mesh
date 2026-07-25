/**
 * 目录加载 / 版本 / 缺 key 三级回退测试 — i18n.md §2.4/§2.5/§3.1。
 */
import { describe, expect, it, vi } from 'vitest';
import {
  CATALOG_ENDPOINT,
  builtinCatalogs,
  computeCatalogVersion,
  loadCatalog,
  resolveMessage,
} from '../catalogLoader';
import type { Catalog } from '../catalogLoader';

describe('computeCatalogVersion(§2.5:内容不可变哈希)', () => {
  it('产出 8 位小写十六进制', () => {
    expect(computeCatalogVersion({ a: '1' })).toMatch(/^[0-9a-f]{8}$/);
  });

  it('确定性:同一内容多次计算结果一致(不依赖 Date/random)', () => {
    const messages = { 'common.save': 'Save', 'common.cancel': 'Cancel' };
    expect(computeCatalogVersion(messages)).toBe(computeCatalogVersion(messages));
  });

  it('与键插入顺序无关', () => {
    expect(computeCatalogVersion({ a: '1', b: '2' })).toBe(
      computeCatalogVersion({ b: '2', a: '1' }),
    );
  });

  it('值或键变化即换版本', () => {
    expect(computeCatalogVersion({ a: '1' })).not.toBe(computeCatalogVersion({ a: '2' }));
    expect(computeCatalogVersion({ a: '1' })).not.toBe(computeCatalogVersion({ b: '1' }));
  });

  it('空目录产出稳定版本', () => {
    expect(computeCatalogVersion({})).toMatch(/^[0-9a-f]{8}$/);
  });
});

describe('resolveMessage(§2.5:请求 locale → en → key 本身 三级回退)', () => {
  const zhCN: Catalog = { locale: 'zh-CN', version: 'v1', messages: { 'common.save': '保存' } };
  const en: Catalog = {
    locale: 'en',
    version: 'v1',
    messages: { 'common.save': 'Save', 'only.en': 'En only' },
  };

  it('主 locale 命中:回退级别 none', () => {
    expect(resolveMessage({ primary: zhCN, fallback: en }, 'common.save')).toEqual({
      text: '保存',
      fallback: 'none',
    });
  });

  it('主 locale 缺 key → 回退 en 同 key:回退级别 en', () => {
    expect(resolveMessage({ primary: zhCN, fallback: en }, 'only.en')).toEqual({
      text: 'En only',
      fallback: 'en',
    });
  });

  it('两级皆缺 → 回退 key 本身:回退级别 key', () => {
    expect(resolveMessage({ primary: zhCN, fallback: en }, 'issue.create.title')).toEqual({
      text: 'issue.create.title',
      fallback: 'key',
    });
  });

  it('主目录缺省时直接走 en 回退级', () => {
    expect(resolveMessage({ fallback: en }, 'common.save')).toEqual({
      text: 'Save',
      fallback: 'en',
    });
  });

  it('en 为请求 locale 时:命中即 none,缺失回退 key(单点回退不中断)', () => {
    expect(resolveMessage({ primary: en, fallback: en }, 'common.save')).toEqual({
      text: 'Save',
      fallback: 'none',
    });
    expect(resolveMessage({ primary: en, fallback: en }, 'nope')).toEqual({
      text: 'nope',
      fallback: 'key',
    });
  });

  it('两级目录均缺省时回退 key', () => {
    expect(resolveMessage({}, 'some.key')).toEqual({ text: 'some.key', fallback: 'key' });
  });
});

describe('loadCatalog(§3.1/§3.2:ETag 版本缓存语义)', () => {
  const fresh: Catalog = { locale: 'zh-CN', version: '9f2c1ab4', messages: { a: '甲' } };
  const cached: Catalog = { locale: 'zh-CN', version: 'old00001', messages: { a: '旧' } };

  it('200 返回完整目录,URL 携带编码后的 locale', async () => {
    const fetcher = vi.fn().mockResolvedValue({ status: 200, body: fresh });
    const result = await loadCatalog('zh-CN', { fetcher, baseUrl: 'https://mesh.test' });
    expect(result).toBe(fresh);
    expect(fetcher).toHaveBeenCalledWith(
      `https://mesh.test${CATALOG_ENDPOINT}?locale=zh-CN`,
      undefined,
    );
  });

  it('无缓存时不携带 If-None-Match', async () => {
    const fetcher = vi.fn().mockResolvedValue({ status: 200, body: fresh });
    await loadCatalog('en', { fetcher });
    expect(fetcher).toHaveBeenCalledWith(`${CATALOG_ENDPOINT}?locale=en`, undefined);
  });

  it('有缓存时携带 If-None-Match: <cached.version>', async () => {
    const fetcher = vi.fn().mockResolvedValue({ status: 304 });
    const result = await loadCatalog('zh-CN', { fetcher, cached });
    expect(fetcher).toHaveBeenCalledWith(expect.any(String), {
      headers: { 'If-None-Match': cached.version },
    });
    expect(result).toBe(cached);
  });

  it('304 无本地缓存(异常组合)→ 拒绝', async () => {
    const fetcher = vi.fn().mockResolvedValue({ status: 304 });
    await expect(loadCatalog('zh-CN', { fetcher })).rejects.toThrow(/304/);
  });

  it('非 200/304 状态 → 抛错并携带状态码', async () => {
    const fetcher = vi.fn().mockResolvedValue({ status: 429 });
    await expect(loadCatalog('zh-CN', { fetcher })).rejects.toThrow(/429/);
  });

  it('200 但目录体结构非法 → 抛错', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue({ status: 200, body: { locale: 'zh-CN' } as unknown as Catalog });
    await expect(loadCatalog('zh-CN', { fetcher })).rejects.toThrow(/Invalid catalog/);
    const fetcher2 = vi.fn().mockResolvedValue({ status: 200, body: undefined });
    await expect(loadCatalog('zh-CN', { fetcher: fetcher2 })).rejects.toThrow(/Invalid catalog/);
    const fetcher3 = vi.fn().mockResolvedValue({
      status: 200,
      body: { locale: 'zh-CN', version: 'v', messages: { a: 1 } } as unknown as Catalog,
    });
    await expect(loadCatalog('zh-CN', { fetcher: fetcher3 })).rejects.toThrow(/Invalid catalog/);
    const fetcher4 = vi.fn().mockResolvedValue({
      status: 200,
      body: { locale: 'zh-CN', version: 'v', messages: ['not-an-object'] } as unknown as Catalog,
    });
    await expect(loadCatalog('zh-CN', { fetcher: fetcher4 })).rejects.toThrow(/Invalid catalog/);
  });

  it('fetcher 网络失败 → 原样上抛(由调用方归类)', async () => {
    const fetcher = vi.fn().mockRejectedValue(new Error('network down'));
    await expect(loadCatalog('zh-CN', { fetcher })).rejects.toThrow('network down');
  });

  it('空 locale → 入参校验抛错', async () => {
    const fetcher = vi.fn();
    await expect(loadCatalog('  ', { fetcher })).rejects.toThrow(/locale/);
    expect(fetcher).not.toHaveBeenCalled();
  });
});

describe('builtinCatalogs(§2.4/§2.5:内置目录,离线可用默认)', () => {
  it('静态内置 en + zh-CN', () => {
    expect(builtinCatalogs.en?.locale).toBe('en');
    expect(builtinCatalogs['zh-CN']?.locale).toBe('zh-CN');
  });

  it('内置目录 version 与其内容哈希一致(发版即换版本)', () => {
    for (const locale of ['en', 'zh-CN']) {
      const catalog = builtinCatalogs[locale];
      expect(catalog.version).toMatch(/^[0-9a-f]{8}$/);
      expect(catalog.version).toBe(computeCatalogVersion(catalog.messages));
    }
  });

  it('内置目录非空且每条消息为非空字符串', () => {
    for (const locale of ['en', 'zh-CN']) {
      const entries = Object.entries(builtinCatalogs[locale].messages);
      expect(entries.length).toBeGreaterThan(100);
      for (const [key, value] of entries) {
        expect(key.length).toBeGreaterThan(0);
        expect(typeof value).toBe('string');
        expect(value.length).toBeGreaterThan(0);
      }
    }
  });
});
