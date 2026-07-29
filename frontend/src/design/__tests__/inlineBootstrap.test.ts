/**
 * index.html 内联防闪烁脚本行为断言(theme.md §2.3 三级链路 + §5.3 安全收敛)。
 *
 * 脚本为 bundle 前同步内联,无法 import TS 模块——本测试从 index.html 原文提取
 * 首个 <script> 主体在 jsdom 中执行,断言其与 themeNegotiation.ts 的行为同构
 * (注入 → 分区 locator(id 校验先于 mode)→ skeleton 标记)。
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

const INDEX_HTML_PATH = resolve(__dirname, '../../../index.html');
const html = readFileSync(INDEX_HTML_PATH, 'utf8');
const match = /<script>([\s\S]*?)<\/script>/.exec(html);
if (match === null) {
  throw new Error('index.html 缺少内联防闪烁脚本');
}
const BOOTSTRAP_SOURCE = match[1];

function runBootstrap(): void {
  // 每个用例独立执行一遍内联脚本(等价首帧)。
  new Function(BOOTSTRAP_SOURCE)();
}

declare global {
  interface Window {
    __MESH_APPEARANCE__?: unknown;
  }
}

beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
  document.documentElement.removeAttribute('data-theme-pending');
  delete window.__MESH_APPEARANCE__;
  window.history.pushState({}, '', '/');
});

afterEach(() => {
  window.history.pushState({}, '', '/');
  localStorage.clear();
  delete window.__MESH_APPEARANCE__;
});

describe('① 精确注入链路', () => {
  it('注入 light/dark → 首帧应用,不触 skeleton', () => {
    window.__MESH_APPEARANCE__ = { mode: 'dark' };
    runBootstrap();
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
    expect(document.documentElement.hasAttribute('data-theme-pending')).toBe(false);
  });

  it('注入值非二值白名单(javascript:/任意串)→ 丢弃,回落后续链路', () => {
    window.__MESH_APPEARANCE__ = { mode: 'javascript:alert(1)' };
    runBootstrap();
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
    expect(document.documentElement.hasAttribute('data-theme-pending')).toBe(true);
  });

  it('注入优先于 locator(正常导航默认链路)', () => {
    localStorage.setItem(
      'mesh.theme.active',
      JSON.stringify({ id: 'localhost:3000:app', mode: 'light' }),
    );
    window.__MESH_APPEARANCE__ = { mode: 'dark' };
    runBootstrap();
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
  });
});

describe('② 分区 locator 链路(id 校验先于 mode)', () => {
  it('id 匹配 + mode 合法 → 应用镜像值', () => {
    const expectedId = `${location.host}:app`;
    localStorage.setItem('mesh.theme.active', JSON.stringify({ id: expectedId, mode: 'dark' }));
    runBootstrap();
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
    expect(document.documentElement.hasAttribute('data-theme-pending')).toBe(false);
  });

  it('/w/{slug} 路由推导 {host}:w:{slug} 分区', () => {
    window.history.pushState({}, '', '/w/acme/board');
    const expectedId = `${location.host}:w:acme`;
    localStorage.setItem('mesh.theme.active', JSON.stringify({ id: expectedId, mode: 'dark' }));
    runBootstrap();
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
  });

  it('双 tab 场景:B 工作区 locator 残留,A 路由不读(id 不符 → skeleton)', () => {
    window.history.pushState({}, '', '/w/acme/board');
    localStorage.setItem(
      'mesh.theme.active',
      JSON.stringify({ id: `${location.host}:w:other`, mode: 'light' }),
    );
    runBootstrap();
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
    expect(document.documentElement.hasAttribute('data-theme-pending')).toBe(true);
  });

  it('mode 非法(即使 id 匹配)→ 丢弃进 skeleton', () => {
    const expectedId = `${location.host}:app`;
    localStorage.setItem(
      'mesh.theme.active',
      JSON.stringify({ id: expectedId, mode: 'javascript:x' }),
    );
    runBootstrap();
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
    expect(document.documentElement.hasAttribute('data-theme-pending')).toBe(true);
  });

  it('非法 JSON 载荷不崩溃,进 skeleton', () => {
    localStorage.setItem('mesh.theme.active', '{broken');
    runBootstrap();
    expect(document.documentElement.hasAttribute('data-theme-pending')).toBe(true);
  });

  it('/invite 路由推导 {host}:invite 分区', () => {
    window.history.pushState({}, '', '/invite/invtk_abc');
    localStorage.setItem(
      'mesh.theme.active',
      JSON.stringify({ id: `${location.host}:invite`, mode: 'dark' }),
    );
    runBootstrap();
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
  });
});

describe('③ skeleton 兜底', () => {
  it('注入与 locator 均无 → 仅 pending 标记,不预置 data-theme', () => {
    runBootstrap();
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
    expect(document.documentElement.getAttribute('data-theme-pending')).toBe('');
  });

  it('locator 缺失(冷启动/登出后)不沿用任何残留', () => {
    localStorage.setItem('mesh.theme', 'dark'); // 遗留镜像键不参与首帧
    runBootstrap();
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
    expect(document.documentElement.hasAttribute('data-theme-pending')).toBe(true);
  });
});

describe('meta theme-color 双声明(§4.2)', () => {
  it('index.html 含亮/暗两条 theme-color,值与表面 token 一致', () => {
    expect(html).toContain(
      '<meta name="theme-color" media="(prefers-color-scheme: light)" content="#ffffff" />',
    );
    expect(html).toContain(
      '<meta name="theme-color" media="(prefers-color-scheme: dark)" content="#0f172a" />',
    );
  });
});
