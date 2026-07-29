/**
 * 暗色视觉回归门禁(theme.md §5.4 / Task 21)。
 *
 * 固定矩阵:6 核心页 × light/dark × 桌面 1024×768 / 平板 768×1024 = 24 用例
 * (视口维度由 playwright.visual.config.ts 的 desktop / tablet 两个 project 提供;
 *  本文件按 page × theme 生成 12 个用例,跨 project 合计 24 个 toHaveScreenshot)。
 *
 * 确定性手段(详见 visual-helpers.ts):
 * - 冻结时钟(page.clock.install 固定 2026-07-25T12:00:00Z)→ 相对时间恒定;
 * - 偏好经 localStorage 显式注入 light/dark(不经协商链),整组切换 data-theme;
 * - 字体锁定(内置 OFL woff2 + 强制覆盖 + fonts.ready);
 * - 动态区 mask(时间戳 / presence / 头像底色 / 连接态等),遮罩清单随用例登记于下。
 *
 * 阈值:maxDiffPixelRatio 0.01 为上限,逐用例只可收紧不可放宽。
 */
import { expect, test, type Locator, type Page } from '@playwright/test';
import { applyFonts, prepareVisualPage } from './visual-helpers';

/** 每个核心页的渲染契约:路由、就绪等待、可选交互、动态区遮罩。 */
interface VisualPage {
  /** 快照名前缀(稳定标识,勿随意改名以免基线失联)。 */
  key: string;
  /** 应用路由(baseURL 相对)。 */
  path: string;
  /** 渲染就绪等待(数据落定的稳定 testid)。 */
  ready: (page: Page) => Promise<void>;
  /** 就绪后、截图前的交互(如聊天选中会话);缺省无。 */
  interact?: (page: Page) => Promise<void>;
  /** 该页专有动态区遮罩(随用例登记);通用遮罩另由 commonMasks 提供。 */
  masks: (page: Page) => Locator[];
}

/**
 * 通用动态区遮罩(所有页共用):
 * - conn-status:WS 连接态色点/文本(connecting→connected 颜色不同);
 * - status-banner-*:离线/重同步横幅(WS 态派生)。
 * 这两类区域随连接时序变化,与主题/布局无关,统一遮罩。
 */
function commonMasks(page: Page): Locator[] {
  return [
    page.locator('[data-testid="conn-status"]'),
    page.locator('[data-testid^="status-banner-"]'),
  ];
}

const PAGES: readonly VisualPage[] = [
  {
    // 看板:卡片/列计数全由 seed 决定,无时间/随机色;遮罩连接态与重同步横幅。
    key: 'board',
    path: '/board',
    ready: async (page) => {
      await page.getByTestId('board-columns').waitFor({ state: 'visible' });
      await page.getByTestId('board-card-issue-1').waitFor({ state: 'visible' });
    },
    masks: (page) => [page.locator('[data-testid="board-resync-banner"]')],
  },
  {
    // issue 详情:评论/活动时间戳为相对时间(冻结时钟下仍 mask 兜底);标签色来自 seed。
    key: 'issue-detail',
    path: '/issues/issue-1',
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
  {
    // 成员:无时间戳;头像底色为 CSS 变量(主题确定),avatar 文字来自 seed。
    key: 'members',
    path: '/members',
    ready: async (page) => {
      await page.locator('table.mesh-members__table').waitFor({ state: 'visible' });
      await page.getByTestId('member-open-member-human-1').waitFor({ state: 'visible' });
    },
    masks: () => [],
  },
  {
    // 聊天:选中首个会话展开对话;会话/消息时间戳为相对时间,遮罩。
    key: 'chat',
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
  {
    // 运行详情(exec-1,终态 completed):elapsed 计时器/进度条遮罩(秒级 tick)。
    key: 'execution',
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
  {
    // 收件箱:行内相对时间戳遮罩;未读红点/计数来自 seed(确定)。
    key: 'inbox',
    path: '/inbox',
    ready: async (page) => {
      await page.getByTestId('inbox-page').waitFor({ state: 'visible' });
      await page.getByTestId('inbox-groups').waitFor({ state: 'visible' });
    },
    masks: (page) => [page.locator('.mesh-inbox__row time')],
  },
];

const THEMES = ['light', 'dark'] as const;

for (const visualPage of PAGES) {
  for (const theme of THEMES) {
    test(`${visualPage.key} ${theme}`, async ({ page }) => {
      await prepareVisualPage(page, theme);
      await page.goto(visualPage.path, { waitUntil: 'domcontentloaded' });
      await visualPage.ready(page);
      if (visualPage.interact !== undefined) {
        await visualPage.interact(page);
      }

      // 主题整组切换断言:暗色用例额外校验 data-theme=dark(亮色同样校验,更严)。
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);

      // 连接态尽力等待 settled(随后仍遮罩,避免连接时序差异进入像素)。
      await page
        .locator('[data-testid="conn-status"] .mesh-status__dot--success')
        .waitFor({ state: 'attached', timeout: 10_000 })
        .catch(() => {});

      await applyFonts(page);

      const mask = [...commonMasks(page), ...visualPage.masks(page)];
      await expect(page).toHaveScreenshot(`${visualPage.key}-${theme}.png`, {
        maxDiffPixelRatio: 0.01,
        mask,
        maskColor: '#000000',
      });
    });
  }
}
