/**
 * locator 回写与登出清理(theme.md §2.3 ②:单键覆盖 + 登出清残留)。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  LEGACY_THEME_MIRROR_KEY,
  THEME_LOCATOR_KEY,
  clearThemeLocators,
  writeThemeLocator,
} from '../themeLocator';

const HREF = 'http://mesh.example/w/acme/board';

describe('writeThemeLocator', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => localStorage.clear());

  it('以路由身份单键覆盖写入 {id, mode}', () => {
    writeThemeLocator('dark', HREF);
    expect(JSON.parse(localStorage.getItem(THEME_LOCATOR_KEY) ?? '')).toEqual({
      id: 'mesh.example:w:acme',
      mode: 'dark',
    });
    writeThemeLocator('light', HREF);
    expect(JSON.parse(localStorage.getItem(THEME_LOCATOR_KEY) ?? '')).toEqual({
      id: 'mesh.example:w:acme',
      mode: 'light',
    });
    // 单键——没有第二个键产生。
    expect(localStorage.length).toBe(1);
  });

  it('存储不可用时静默降级,不抛错', () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('denied');
    });
    expect(() => writeThemeLocator('dark', HREF)).not.toThrow();
    spy.mockRestore();
  });
});

describe('clearThemeLocators — 登出清理(防下一账号串用)', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => localStorage.clear());

  it('删除 locator、遗留镜像键与历史分区格式残留', () => {
    localStorage.setItem(THEME_LOCATOR_KEY, JSON.stringify({ id: 'x:app', mode: 'dark' }));
    localStorage.setItem(LEGACY_THEME_MIRROR_KEY, 'dark');
    localStorage.setItem(`${THEME_LOCATOR_KEY}:legacy-partition`, JSON.stringify({ mode: 'dark' }));
    localStorage.setItem('mesh.settings.v1', '{"preferences":{}}'); // 无关键保留

    clearThemeLocators();

    expect(localStorage.getItem(THEME_LOCATOR_KEY)).toBeNull();
    expect(localStorage.getItem(LEGACY_THEME_MIRROR_KEY)).toBeNull();
    expect(localStorage.getItem(`${THEME_LOCATOR_KEY}:legacy-partition`)).toBeNull();
    expect(localStorage.getItem('mesh.settings.v1')).not.toBeNull();
  });

  it('幂等:空存储下不抛错', () => {
    expect(() => clearThemeLocators()).not.toThrow();
  });

  it('存储不可用时静默降级', () => {
    const spy = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new Error('denied');
    });
    expect(() => clearThemeLocators()).not.toThrow();
    spy.mockRestore();
  });
});
