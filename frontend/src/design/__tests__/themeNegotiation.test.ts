/**
 * 协商链与 locator 白名单解析真源表(theme.md §2.2 / §2.3 ②,§5.1 验收)。
 */
import { describe, expect, it } from 'vitest';

import {
  expectedRouteId,
  isThemeMode,
  parseThemeLocator,
  resolveThemeChain,
} from '../themeNegotiation';

describe('resolveThemeChain — §2.2 真源表', () => {
  it('user=dark 终止于用户级(忽略工作区)', () => {
    expect(
      resolveThemeChain({ userTheme: 'dark', workspaceDefault: 'light', systemPrefersDark: false }),
    ).toEqual({ mode: 'dark', source: 'user' });
  });

  it('user=light 终止于用户级', () => {
    expect(
      resolveThemeChain({ userTheme: 'light', workspaceDefault: 'dark', systemPrefersDark: true }),
    ).toEqual({ mode: 'light', source: 'user' });
  });

  it('显式 user=system 本级终止跟随 OS,不回退工作区默认(§2.1)', () => {
    expect(
      resolveThemeChain({ userTheme: 'system', workspaceDefault: 'dark', systemPrefersDark: false }),
    ).toEqual({ mode: 'light', source: 'user' });
    expect(
      resolveThemeChain({ userTheme: 'system', workspaceDefault: 'light', systemPrefersDark: true }),
    ).toEqual({ mode: 'dark', source: 'user' });
  });

  it('user=null 跳过第 1 级,采用工作区默认', () => {
    expect(
      resolveThemeChain({ userTheme: null, workspaceDefault: 'dark', systemPrefersDark: false }),
    ).toEqual({ mode: 'dark', source: 'workspace' });
  });

  it('user=absent 跳过第 1 级,采用工作区默认', () => {
    expect(
      resolveThemeChain({ userTheme: undefined, workspaceDefault: 'light', systemPrefersDark: true }),
    ).toEqual({ mode: 'light', source: 'workspace' });
  });

  it('user=null + 工作区默认 system → 落系统解析', () => {
    expect(
      resolveThemeChain({ userTheme: null, workspaceDefault: 'system', systemPrefersDark: true }),
    ).toEqual({ mode: 'dark', source: 'system' });
  });

  it('user=null + 工作区默认 absent(默认 system)→ 落系统解析', () => {
    expect(
      resolveThemeChain({ userTheme: null, workspaceDefault: null, systemPrefersDark: false }),
    ).toEqual({ mode: 'light', source: 'system' });
    expect(
      resolveThemeChain({ userTheme: null, workspaceDefault: undefined, systemPrefersDark: true }),
    ).toEqual({ mode: 'dark', source: 'system' });
  });
});

describe('isThemeMode 白名单守卫', () => {
  it('三值合法,其余一律非法', () => {
    expect(isThemeMode('light')).toBe(true);
    expect(isThemeMode('dark')).toBe(true);
    expect(isThemeMode('system')).toBe(true);
    expect(isThemeMode(null)).toBe(false);
    expect(isThemeMode('neon')).toBe(false);
    expect(isThemeMode(42)).toBe(false);
    expect(isThemeMode(undefined)).toBe(false);
  });
});

describe('expectedRouteId — 路由身份分区表(R3-H3)', () => {
  it('/w/{slug}/… → {host}:w:{slug}', () => {
    expect(expectedRouteId('http://mesh.example/w/acme/board')).toBe('mesh.example:w:acme');
    expect(expectedRouteId('https://mesh.example:8080/w/acme/issues/by-identifier/ACM-1')).toBe(
      'mesh.example:8080:w:acme',
    );
  });

  it('/invite 公开入口 → {host}:invite', () => {
    expect(expectedRouteId('http://mesh.example/invite/invtk_abc')).toBe('mesh.example:invite');
    expect(expectedRouteId('http://mesh.example/invite?token=invtk_abc')).toBe(
      'mesh.example:invite',
    );
  });

  it('其余已登录应用路由 → {host}:app', () => {
    expect(expectedRouteId('http://mesh.example/settings')).toBe('mesh.example:app');
    expect(expectedRouteId('http://mesh.example/')).toBe('mesh.example:app');
  });

  it('其余公开页 → {host}:anon', () => {
    expect(expectedRouteId('http://mesh.example/login')).toBe('mesh.example:anon');
    expect(expectedRouteId('http://mesh.example/register')).toBe('mesh.example:anon');
  });

  it('不可解析 URL 不崩溃', () => {
    expect(expectedRouteId('not a url')).toBe('unknown:app');
  });
});

describe('parseThemeLocator — 白名单与分区校验(§5.3)', () => {
  const expectedId = 'mesh.example:w:acme';

  it('id 匹配且 mode 合法 → 返回 mode', () => {
    const raw = JSON.stringify({ id: expectedId, mode: 'dark' });
    expect(parseThemeLocator(raw, expectedId)).toBe('dark');
  });

  it('null / 空串 → null', () => {
    expect(parseThemeLocator(null, expectedId)).toBeNull();
    expect(parseThemeLocator('', expectedId)).toBeNull();
  });

  it('非法 JSON → null(不崩溃)', () => {
    expect(parseThemeLocator('{oops', expectedId)).toBeNull();
    expect(parseThemeLocator('javascript:alert(1)', expectedId)).toBeNull();
  });

  it('id 与期望不符(跨 tab/跨路由残留)→ null,id 校验先于 mode', () => {
    const raw = JSON.stringify({ id: 'mesh.example:w:other', mode: 'dark' });
    expect(parseThemeLocator(raw, expectedId)).toBeNull();
  });

  it('mode 非 light|dark → null(即便 id 匹配)', () => {
    expect(parseThemeLocator(JSON.stringify({ id: expectedId, mode: 'system' }), expectedId)).toBeNull();
    expect(
      parseThemeLocator(JSON.stringify({ id: expectedId, mode: 'javascript:x' }), expectedId),
    ).toBeNull();
    expect(parseThemeLocator(JSON.stringify({ id: expectedId }), expectedId)).toBeNull();
  });

  it('非对象 JSON → null', () => {
    expect(parseThemeLocator('"dark"', expectedId)).toBeNull();
    expect(parseThemeLocator('42', expectedId)).toBeNull();
  });
});
