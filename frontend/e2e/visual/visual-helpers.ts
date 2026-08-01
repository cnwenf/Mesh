/**
 * 视觉回归共享基建(theme.md §5.4)。
 *
 * 同时服务 `theme-visual.spec.ts`(暗色视觉回归门禁,toHaveScreenshot 基线比对)与
 * `forced-colors.spec.ts`(强制高对比仿真验收)——二者共享同一页面注册表 `PAGES`,
 * 保证「同一核心页面集」(§5.4)。
 *
 * 确定性手段:
 * - 会话/偏好经 addInitScript 直写 localStorage:`mesh.auth.v1`(Bearer)+
 *   `mesh.settings.v1`(theme 显式 light/dark,不经协商链);
 * - 字体锁定:e2e/fixtures/fonts/ 内置 OFL woff2,经视觉 mock(8911)分发,
 *   @font-face 分层(latin / 简中)+ `* { font-family: 'Mesh Visual Font' }` 强制
 *   覆盖 + `font-display: block` + document.fonts.ready 等待,杜绝字体回退抖动;
 * - 冻结时钟:page.clock.install 固定时间戳,相对时间恒定(动态时间区另以 mask 排除);
 * - 预热(warmUpPages):首跑前预编译核心路由模块,消除 Vite 按需编译/依赖再优化与
 *   被测导航的竞争(冷启动偶发 ErrorBoundary 的确定性漏洞)。
 */
import type { Browser, Locator, Page } from '@playwright/test';

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

/** 单个核心页的可视化契约。 */
export interface VisualPageSpec {
  /** 稳定英文标识,用于截图基线文件名(勿改,以免基线失联)。 */
  snapshotKey: string;
  /** 应用路由(baseURL 相对)。 */
  path: string;
  /** 渲染就绪等待(数据落定的稳定 testid)。 */
  ready: (page: Page) => Promise<void>;
  /** 就绪后、截图前的交互(如聊天选中会话);缺省无。 */
  interact?: (page: Page) => Promise<void>;
  /** 该页专有动态区遮罩(随用例登记);通用遮罩见 commonMasks。 */
  masks: (page: Page) => Locator[];
}

/**
 * 核心页注册表(键为中文页名,供 navigateToPage 与 forced-colors 共用)。
 * design-quality §13.5 的 13 个核心页。注册表也是视觉、媒体偏好、axe、reflow
 * 与证据矩阵的单一真源；新增核心页必须在这里登记，避免门禁之间页面集漂移。
 */
export const PAGES: Record<string, VisualPageSpec> = {
  登录: {
    snapshotKey: 'login',
    path: '/login',
    ready: async (page) => {
      await page.getByTestId('login-account-submit').waitFor({ state: 'visible' });
    },
    masks: () => [],
  },
  工作台: {
    snapshotKey: 'home',
    path: '/',
    ready: async (page) => {
      await page.getByTestId('home-greeting').waitFor({ state: 'visible' });
      await page.getByTestId('home-dashboard').waitFor({ state: 'visible' });
    },
    masks: (page) => [page.locator('.mesh-home time')],
  },
  'issue 列表': {
    snapshotKey: 'issues',
    path: '/issues',
    ready: async (page) => {
      await page.getByTestId('issue-row-MESH-1').waitFor({ state: 'visible' });
    },
    masks: () => [],
  },
  看板: {
    // 卡片/列计数全由 seed 决定,无时间/随机色;遮罩重同步横幅。
    snapshotKey: 'board',
    path: '/board',
    ready: async (page) => {
      const compact = await page.evaluate(() => matchMedia('(max-width: 599px)').matches);
      await page
        .getByTestId(compact ? 'board-compact' : 'board-columns')
        .waitFor({ state: 'visible' });
      await page.getByTestId('board-card-issue-1').waitFor({ state: 'visible' });
    },
    masks: (page) => [page.locator('[data-testid="board-resync-banner"]')],
  },
  'issue 详情': {
    // 评论/活动时间戳为相对时间(冻结时钟下仍 mask 兜底);标签色来自 seed。
    // 深链走规范 by-identifier 形态(§3.4 一跳可解析):fixture 解析返回 UUID 主键
    // 形态后直达详情渲染;旧 `/issues/issue-1` 扁平形态在 MES-79 路由态下构成
    // identifier 重定向自循环,不再使用。
    snapshotKey: 'issue-detail',
    path: '/issues/by-identifier/MESH-1',
    ready: async (page) => {
      await page.getByTestId('issue-detail').waitFor({ state: 'visible' });
      await page.getByTestId('comments-panel').waitFor({ state: 'visible' });
      await page.getByTestId('attachments-empty').waitFor({ state: 'visible' });
    },
    masks: (page) => [
      page.locator('[data-testid="comments-timeline"] time'),
      page.locator('[data-testid="issue-detail-activity"] time'),
    ],
  },
  成员: {
    // 无时间戳;头像底色为 CSS 变量(主题确定),文字来自 seed。
    snapshotKey: 'members',
    path: '/members',
    ready: async (page) => {
      const compact = await page.evaluate(() => matchMedia('(max-width: 599px)').matches);
      await page
        .getByTestId(compact ? 'member-card-member-human-1' : 'member-open-member-human-1')
        .waitFor({ state: 'visible' });
    },
    masks: () => [],
  },
  聊天: {
    // 选中首个会话展开对话;会话/消息时间戳为相对时间,遮罩。
    snapshotKey: 'chat',
    path: '/chat',
    ready: async (page) => {
      await page.getByTestId('chat-session-panel').waitFor({ state: 'visible' });
      await page.getByTestId('chat-session-sess-1').waitFor({ state: 'visible' });
    },
    interact: async (page) => {
      await page.getByTestId('chat-session-sess-1').click();
      await page.getByTestId('chat-messages').waitFor({ state: 'visible' });
      await page.getByTestId('chat-message-msg-1').waitFor({ state: 'visible' });
    },
    masks: (page) => [
      page.locator('[data-testid^="chat-session-"] .mesh-chat__session-time'),
      page.locator('[data-testid^="chat-message-"] .mesh-chat__bubble-time'),
    ],
  },
  运行详情: {
    // exec-1 终态 completed:elapsed 计时器/进度条遮罩(秒级 tick)。
    snapshotKey: 'execution',
    path: '/executions/exec-1',
    ready: async (page) => {
      await page.getByTestId('execution-detail-page').waitFor({ state: 'visible' });
      await page.getByTestId('execution-panel-logs').waitFor({ state: 'visible' });
    },
    masks: (page) => [
      page.locator('[data-testid="execution-elapsed"]'),
      page.locator('[data-testid="execution-progress"]'),
    ],
  },
  收件箱: {
    // 行内相对时间戳遮罩;未读红点/计数来自 seed(确定)。
    snapshotKey: 'inbox',
    path: '/inbox',
    ready: async (page) => {
      await page.getByTestId('inbox-page').waitFor({ state: 'visible' });
      await page.getByTestId('inbox-groups').waitFor({ state: 'visible' });
    },
    masks: (page) => [page.locator('.mesh-inbox__row time')],
  },
  自动值守: {
    snapshotKey: 'autopilots',
    path: '/autopilots',
    ready: async (page) => {
      await page.getByTestId('autopilot-row-autopilot-1').waitFor({ state: 'visible' });
    },
    masks: (page) => [page.locator('[data-testid^="autopilot-last-run-"]')],
  },
  集成: {
    snapshotKey: 'integrations',
    path: '/integrations',
    ready: async (page) => {
      await page.getByTestId('integration-row-integration-1').waitFor({ state: 'visible' });
    },
    masks: () => [],
  },
  洞察: {
    snapshotKey: 'insights',
    path: '/insights',
    ready: async (page) => {
      await page.getByTestId('insights-range').waitFor({ state: 'visible' });
    },
    masks: () => [],
  },
  设置: {
    snapshotKey: 'settings',
    path: '/settings/appearance',
    ready: async (page) => {
      await page.getByTestId('theme-select').waitFor({ state: 'visible' });
    },
    masks: () => [],
  },
};

/** 由内置 woff2(视觉 mock 分发)构造分层 @font-face + 强制覆盖样式表。 */
export function buildFontFaceCss(): string {
  const faces: string[] = [];
  for (const weight of FONT_WEIGHTS) {
    faces.push(
      `@font-face{font-family:'${VISUAL_FONT_FAMILY}';font-style:normal;font-weight:${weight};` +
        `font-display:block;src:url(${VISUAL_MOCK_BASE}/visual/fonts/noto-sans-sc-latin-${weight}-normal.woff2) format('woff2');` +
        `unicode-range:${LATIN_RANGE};}`,
    );
    faces.push(
      `@font-face{font-family:'${VISUAL_FONT_FAMILY}';font-style:normal;font-weight:${weight};` +
        `font-display:block;src:url(${VISUAL_MOCK_BASE}/visual/fonts/noto-sans-sc-chinese-simplified-${weight}-normal.woff2) format('woff2');` +
        `unicode-range:${CJK_RANGE}, U+0000-10FFFF;}`,
    );
  }
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
      // /login 是核心视觉矩阵中的公开页。每次文档初始化按目标 path 建立准确
      // 会话态，使同一注册表既能跑公开页，也能跑受保护页，且循环导航不串态。
      if (window.location.pathname === '/login') {
        window.localStorage.removeItem('mesh.auth.v1');
      } else {
        window.localStorage.setItem(
          'mesh.auth.v1',
          JSON.stringify({ state: { token, refreshToken: null }, version: 0 }),
        );
      }
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
 * 导航到注册表中的页面并完成就绪等待 + 交互。不含会话注入/时钟(由调用方负责),
 * 以便 forced-colors 等无需截图的用例复用同一路由契约。
 */
export async function navigateToPage(page: Page, name: string): Promise<void> {
  const spec = PAGES[name];
  if (spec === undefined) {
    throw new Error(`unknown visual page: ${name}`);
  }
  await page.goto(spec.path, { waitUntil: 'domcontentloaded' });
  await spec.ready(page);
  if (spec.interact !== undefined) {
    await spec.interact(page);
  }
}

/**
 * 等待页面进入稳定态:连接态尽力 settled(随后截图用例仍遮罩),并锁定字体。
 * 对无需截图的用例(forced-colors)同样安全:仅做幂等的就绪等待。
 */
export async function waitForStable(page: Page): Promise<void> {
  await page
    .locator('[data-testid="conn-status"] .mesh-status__dot--success')
    .waitFor({ state: 'attached', timeout: 10_000 })
    .catch(() => {});
  await applyFonts(page);
}

/**
 * 通用动态区遮罩(所有截图用例共用):
 * - conn-status:WS 连接态色点/文本(connecting→connected 颜色不同);
 * - status-banner-*:离线/重同步横幅(WS 态派生)。
 */
export function commonMasks(page: Page): Locator[] {
  return [
    page.locator('[data-testid="conn-status"]'),
    page.locator('[data-testid^="status-banner-"]'),
  ];
}

/**
 * 准备一个确定性的可视化页面上下文:冻结时钟 → 注入会话/偏好。
 * 调用方随后 navigateToPage → waitForStable → (mask →)toHaveScreenshot。
 */
export async function prepareVisualPage(page: Page, theme: 'light' | 'dark'): Promise<void> {
  await page.clock.install({ time: FIXED_NOW });
  await injectSession(page, theme);
}

/**
 * 预热:用一次性页面访问全部核心路由,触发 Vite 按需编译/依赖再优化,使被测导航
 * 不再与冷启动编译竞争(消除首跑偶发 ErrorBoundary)。不冻结时钟、不截图。
 */
export async function warmUpPages(browser: Browser): Promise<void> {
  const warm = await browser.newPage();
  try {
    await injectSession(warm, 'light');
    for (const spec of Object.values(PAGES)) {
      await warm.goto(spec.path, { waitUntil: 'domcontentloaded' }).catch(() => {});
      // 让该路由触发的懒加载模块完成编译;Playwright 侧真实等待,不受页面时钟影响。
      await warm.waitForTimeout(250);
    }
  } finally {
    await warm.close();
  }
}
