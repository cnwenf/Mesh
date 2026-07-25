/**
 * 桶导出测试:src/i18n 的公共 API 面(negotiate/loader/format/missing/provider/catalogs)。
 */
import { describe, expect, it } from 'vitest';
import * as I18n from '../index';

describe('src/i18n 桶导出', () => {
  it('协商 / 目录 / 格式化 / 缺失上报 / Provider 全部经桶暴露', () => {
    // negotiate
    expect(I18n.SUPPORTED_LOCALES).toEqual(['zh-CN', 'en']);
    expect(I18n.FALLBACK_LOCALE).toBe('en');
    expect(typeof I18n.parseAcceptLanguage).toBe('function');
    expect(typeof I18n.matchSupported).toBe('function');
    expect(typeof I18n.negotiateLocale).toBe('function');
    // catalogLoader
    expect(typeof I18n.computeCatalogVersion).toBe('function');
    expect(typeof I18n.resolveMessage).toBe('function');
    expect(typeof I18n.loadCatalog).toBe('function');
    expect(I18n.builtinCatalogs.en).toBeDefined();
    expect(I18n.builtinCatalogs['zh-CN']).toBeDefined();
    expect(typeof I18n.CATALOG_ENDPOINT).toBe('string');
    // format
    expect(typeof I18n.formatDateTime).toBe('function');
    expect(typeof I18n.formatDate).toBe('function');
    expect(typeof I18n.formatNumber).toBe('function');
    expect(typeof I18n.formatRelativeTime).toBe('function');
    expect(typeof I18n.formatWithZoneAnnotation).toBe('function');
    expect(typeof I18n.parseLocalToUTC).toBe('function');
    expect(typeof I18n.isValidTimezone).toBe('function');
    expect(typeof I18n.isValidLocaleTag).toBe('function');
    // missing
    expect(typeof I18n.createMissingReporter).toBe('function');
    expect(typeof I18n.MISSING_REPORT_PATH).toBe('string');
    // provider
    expect(typeof I18n.I18nProvider).toBe('function');
    expect(typeof I18n.useT).toBe('function');
    expect(typeof I18n.defaultMissingReporter.report).toBe('function');
  });
});
