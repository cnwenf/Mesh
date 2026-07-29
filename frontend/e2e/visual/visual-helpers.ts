/**
 * 视觉回归确定性基建(theme.md §5.4)。
 *
 * 提供每个用例共享的会话/偏好注入、字体锁定与冻结时钟,确保同一 seed 下
 * 多次运行像素级一致:
 * - 会话与偏好经 addInitScript 直写 localStorage(不经 UI 登录协商链):
 *   `mesh.auth.v1`(Bearer token)+ `mesh.settings.v1`(theme 显式 light/dark);
 * - 字体锁定:e2e/fixtures/fonts/ 内置 OFL woff2,经视觉 mock(8911)分发,
 *   @font-face 分层(latin / 简中)+ `* { font-family: 'Mesh Visual Font' }` 强制
 *   覆盖 + `font-display: block` + document.fonts.ready 等待,杜绝字体回退抖动;
 * - 冻结时钟:page.clock.install 固定时间戳,相对时间恒定(动态时间区另以 mask 排除)。
 */
import type { Page } from '@playwright/test';

/** 视觉专用 mock 服务端基址(独立端口,勿与默认套件 8901 冲突)。 */
export const VISUAL_MOCK_BASE = 'http://127.0.0.1:8911';

/** dev 鉴权 token(与视觉 mock 的 mesh-dev: 前缀校验一致;仅 e2e 自测用,非密钥)。 */
export const VISUAL_TOKEN = 'mesh-dev:00000000-0000-0000-0000-000000000001';

/** 冻结时钟固定时间戳:2026-07-25T12:00:00Z(晚于 seed 数据时间,相对时间恒定)。 */
export const FIXED_NOW = Date.UTC(2026, 6, 25, 12, 0, 0);

/** 视觉字体族名(@font-face 与强制覆盖共用)。 */
const VISUAL_FONT_FAMILY = 'Mesh Visual Font';

/** latin 子集覆盖区段(基本 ASCII / 拉丁扩展 / 常用排版符号)。 */
const LATIN_RANGE =
  'U+0000-024F, U+0300-036F, U+1E00-1EFF, U+2000-206F, U+20A0-20CF, U+2100-214F, U+2200-22FF';

/** 简中子集覆盖区段(CJK 统一表意文字 / 标点 / 全半角 / 扩展 A)。 */
const CJK_RANGE =
  'U+2E80-9FFF, U+F900-FAFF, U+FE30-FE4F, U+FF00-FFEF, U+3000-303F, U+3400-4DBF, U+20000-2A6DF';

const FONT_WEIGHTS = [400, 500, 700] as const;

/** 由内置 woff2(视觉 mock 分发)构造分层 @font-face + 强制覆盖样式表。 */
export function buildFontFaceCss(): string {
  const faces: string[] = [];
  for (const weight of FONT_WEIGHTS) {
    // latin 层:窄区段,优先命中 ASCII/拉丁字形。
    faces.push(
      `@font-face{font-family:'${VISUAL_FONT_FAMILY}';font-style:normal;font-weight:${weight};` +
        `font-display:block;src:url(${VISUAL_MOCK_BASE}/visual/fonts/noto-sans-sc-latin-${weight}-normal.woff2) format('woff2');` +
        `unicode-range:${LATIN_RANGE};}`,
    );
    // 简中层:CJK 区段;并以全区段兜底,确保 UI 中文文案不回落系统字体。
    faces.push(
      `@font-face{font-family:'${VISUAL_FONT_FAMILY}';font-style:normal;font-weight:${weight};` +
        `font-display:block;src:url(${VISUAL_MOCK_BASE}/visual/fonts/noto-sans-sc-chinese-simplified-${weight}-normal.woff2) format('woff2');` +
        `unicode-range:${CJK_RANGE}, U+0000-10FFFF;}`,
    );
  }
  // 强制覆盖:任何元素一律使用视觉字体,屏蔽运行机系统字体差异。
  const override = `*{font-family:'${VISUAL_FONT_FAMILY}' !important;}`;
  return faces.join('\n') + '\n' + override;
}

/**
 * 注入会话态(Bearer token)与偏好(theme 显式 light/dark,locale zh-CN,tz UTC)。
 * 经 addInitScript 在每个文档脚本执行前写入 localStorage,ThemeProvider 直接读取
 * 显式偏好整组切换 data-theme,不经工作区协商链。
 */
export async function injectSession(page: Page, theme: 'light' | 'dark'): Promise<void> {
  await page.addInitScript(
    ({ token, theme: mode }) => {
      window.localStorage.setItem(
        'mesh.auth.v1',
        JSON.stringify({ state: { token, refreshToken: null }, version: 0 }),
      );
      window.localStorage.setItem(
        'mesh.settings.v1',
        JSON.stringify({
          state: { preferences: { theme: mode, locale: 'zh-CN', timezone: 'UTC' } },
          version: 2,
        }),
      );
    },
    { token: VISUAL_TOKEN, theme },
  );
}

/** 注入字体样式表并等待字体就绪(杜绝字体回退/渐显抖动)。 */
export async function applyFonts(page: Page): Promise<void> {
  await page.addStyleTag({ content: buildFontFaceCss() });
  await page.evaluate(() => document.fonts.ready);
}

/**
 * 准备一个确定性的可视化页面上下文:冻结时钟 → 注入会话/偏好。
 * 调用方随后 goto → 等待渲染就绪 → applyFonts → (mask →)toHaveScreenshot。
 */
export async function prepareVisualPage(page: Page, theme: 'light' | 'dark'): Promise<void> {
  await page.clock.install({ time: FIXED_NOW });
  await injectSession(page, theme);
}
