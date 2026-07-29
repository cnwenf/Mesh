/**
 * search 数据层桶导出回归(index.ts):防新增模块漏挂桶导致消费方 import 落空。
 */
import { describe, expect, it } from 'vitest';
import * as search from '../index';

describe('search 数据层桶导出', () => {
  it('导出契约/本地存储/组装/解析/身份/文案各层核心符号', () => {
    expect(typeof search.searchWorkspace).toBe('function');
    expect(typeof search.listAllFavorites).toBe('function');
    expect(typeof search.readRecents).toBe('function');
    expect(typeof search.buildEmptyQueryRows).toBe('function');
    expect(typeof search.resolveTarget).toBe('function');
    expect(typeof search.resolveFavoriteTargets).toBe('function');
    expect(typeof search.collectValidRecentKeys).toBe('function');
    expect(typeof search.usePaletteIdentity).toBe('function');
    expect(typeof search.useEntitySearch).toBe('function');
    expect(typeof search.entitySubtitle).toBe('function');
    expect(search.IDENTIFIER_QUERY_PATTERN).toBeInstanceOf(RegExp);
  });
});
