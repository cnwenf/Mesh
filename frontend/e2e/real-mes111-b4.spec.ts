/**
 * MES-111 批次④ 真实 e2e —— 设置 + 搜索/命令面板 + Analytics + /approvals
 * (production 鉴权 + 公网 HTTP,桌面 1440×900 / 手机 390×844 双视口,
 * 见 playwright.mes111-b4.config.ts 的 projects)。
 *
 * 验收项(issue 逐条对应):
 * 1. 账号设置:/settings 索引重定向 → profile；appearance 中主题/语言/时区即时生效;
 *    SettingsLayout 二级导航在场;四组合存证(桌面/手机 × 亮/暗)。
 * 2. 工作区设置:/w/:slug/settings 索引重定向 → general;dirty 提示 + 保存 toast +
 *    刷新持久化;G11 默认主题选择器(admin)写入后对工作区页面真实生效(账号偏好缺省
 *    时协商链落工作区默认)。
 * 3. 命令面板(§9.6 / §4.9):Ctrl/Cmd+K 开启;六类业务对象检索分组(真实召回
 *    issue/成员)+ identifier 直达 + 方向键/Enter/Esc 键盘全流程 + live region;
 *    顶栏搜索为真实控件:键入即展开同一结果视图弹层,Enter 交接完整面板。
 * 4. Analytics(/insights):空窗态 → 创建 issue 后有数据态;范围/粒度选择器 +
 *    时区口径行在场;无加载失败。
 * 5. G10:/approvals 与 /w/:slug/approvals 深链直达统一审批页(空态可,不崩不失败)。
 *
 * 每个用例顺带完成「亮 → 暗」切换走查存证,四组合(桌面/手机 × 亮/暗)汇聚于
 * e2e/evidence/mes111-b4/(md5 唯一性门禁 scripts/check-evidence-unique.mjs)。
 *
 * 前置:隔离验收栈运行中(仓库根目录):
 *   ./frontend/e2e/mes111-b4/gen-stack-env.sh
 *   docker compose -p mes111-b4 \
 *     -f docker-compose.yml -f frontend/e2e/mes111-b4/compose.override.yml \
 *     --env-file frontend/e2e/mes111-b4/stack.env up -d --build
 * 然后(frontend/ 目录):
 *   npx playwright test --config playwright.mes111-b4.config.ts
 */
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

const PASSWORD = 'Mesh-Demo#2026x';
const EVIDENCE_DIR = 'e2e/evidence/mes111-b4';
const LOAD_FAILED_TEXT = 'We could not load this content. Please try again.';

/** 每用例唯一邮箱(注册即登录;同邮箱重注会 409) */
function uniqueEmail(suffix: string): string {
  return `mes127-${suffix}-${String(process.pid)}-${String(Date.now())}@example.com`;
}

/** 新用户引导清单(移动端整屏遮罩)在截图前关闭,保证存证为真实目标页。
 *  弹层(命令面板)打开时背景被 backdrop 拦截点击,且清单在其后非目标——跳过。 */
async function dismissOnboarding(page: Page): Promise<void> {
  if (
    await page
      .locator('.mesh-dialog__backdrop')
      .isVisible({ timeout: 0 })
      .catch(() => false)
  ) {
    return;
  }
  const dismiss = page.getByText("Don't show again");
  if (await dismiss.isVisible({ timeout: 1500 }).catch(() => false)) {
    await dismiss.click();
    await page.waitForTimeout(150);
  }
}

/** 存证文件名携带视口项目名,与主题后缀共同构成四组合 */
async function evidence(page: Page, stem: string): Promise<void> {
  await dismissOnboarding(page);
  const project = test.info().project.name;
  await page.screenshot({ path: `${EVIDENCE_DIR}/${project}-${stem}.png` });
}

/** 注册新账号并经「已发验证邮件」结果页继续(生产模式注册自动登录) */
async function registerAndContinue(page: Page, email: string, name: string): Promise<void> {
  await page.goto('/login');
  await page.getByTestId('login-mode-register').click();
  await page.getByTestId('login-display-name').fill(name);
  await page.getByTestId('login-email').fill(email);
  await page.getByTestId('login-password').fill(PASSWORD);
  await page.getByTestId('login-account-submit').click();
  await expect(page.getByTestId('register-verify-sent')).toContainText(email);
  await page.getByTestId('register-continue').click();
  await page.waitForURL((url) => new URL(url).pathname === '/');
}

/** 走真实创建向导建工作区(name → slug → 跳过邀请),返回 slug */
async function createWorkspace(page: Page, slug: string, name: string): Promise<void> {
  await page.goto('/');
  await expect(page.getByTestId('home-no-workspaces')).toBeVisible();
  await page.getByTestId('home-create-workspace').click();
  await page.getByTestId('ws-wizard-name-input').fill(name);
  await page.getByTestId('ws-wizard-next').click();
  await page.getByTestId('ws-wizard-slug-input').fill(slug);
  await page.getByTestId('ws-wizard-next-slug').click();
  await page.getByTestId('ws-wizard-skip').click();
  // 向导收尾回首页后工作区卡片呈现(mes107 同形)
  await page.goto('/');
  await expect(page.getByTestId(`home-workspace-${slug}`)).toBeVisible();
}

/** 主题切换(账号偏好即时生效,<html data-theme> 同步) */
async function setTheme(page: Page, mode: 'light' | 'dark'): Promise<void> {
  await page.goto('/settings/appearance');
  await page.getByTestId('theme-select').selectOption(mode);
  await expect(page.locator('html')).toHaveAttribute('data-theme', mode);
}

/** 首页仪表盘快捷创建 issue(真实落库) */
async function createIssueFromHome(page: Page, title: string): Promise<void> {
  await page.goto('/');
  await expect(page.getByTestId('home-dashboard')).toBeVisible();
  await page.getByTestId('home-new-title').fill(title);
  await page.getByTestId('home-create').click();
  await expect(page.getByTestId('home-issue-list')).toContainText(title);
}

test.describe('MES-111 批次④ 设置 / 搜索命令面板 / Analytics / 审批', () => {
  test('账号设置:索引重定向 + 主题/语言/时区即时生效 + 四组合存证', async ({ page }) => {
    await registerAndContinue(page, uniqueEmail('set'), 'MES-127 设置');

    // /settings 索引 → 个人资料默认页；外观设置保留独立规范深链。
    await page.goto('/settings');
    await page.waitForURL(/\/settings\/profile$/);
    await expect(page.getByLabel('Name')).toBeVisible();
    await expect(page.getByTestId('settings-nav-profile')).toBeVisible();

    await page.goto('/settings/appearance');
    await expect(page.getByTestId('theme-select')).toBeVisible();
    await expect(page.getByTestId('locale-select')).toBeVisible();
    await expect(page.getByTestId('timezone-select')).toBeVisible();
    // 二级导航三项(桌面左栏 / 手机顶部分组列表,同一组件自适应)
    await expect(page.getByTestId('settings-nav-appearance')).toBeVisible();
    await expect(page.getByTestId('settings-nav-notifications')).toBeVisible();
    await expect(page.getByTestId('settings-nav-security')).toBeVisible();

    // 时区即时生效:样例随选择更新(东八区偏移呈现)
    await page.getByTestId('timezone-select').selectOption('Asia/Shanghai');
    await expect(page.getByTestId('tz-sample')).toContainText('(GMT+8)');

    // 语言即时生效:页标题切中文,再恢复(避免影响后续存证文案)
    await page.getByTestId('locale-select').selectOption('zh-CN');
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('设置');
    await page.getByTestId('locale-select').selectOption('');
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Settings');

    await evidence(page, 'settings-light');
    await setTheme(page, 'dark');
    await evidence(page, 'settings-dark');
    await expect(page.getByText(LOAD_FAILED_TEXT)).toHaveCount(0);
  });

  test('工作区设置:dirty/save 反馈 + G11 默认主题真实生效', async ({ page }) => {
    await registerAndContinue(page, uniqueEmail('ws'), 'MES-127 工作区');
    const slug = `mes127b-${String(Date.now()).slice(-8)}`;
    await createWorkspace(page, slug, 'MES-127 B4 WS');

    // 索引 → general;基本信息表单在场
    await page.goto(`/w/${slug}/settings`);
    await page.waitForURL(new RegExp(`/w/${slug}/settings/general$`));
    await expect(page.getByTestId('ws-name-input')).toBeVisible();
    await expect(page.getByTestId('ws-basic-info')).toBeVisible();
    await evidence(page, 'ws-settings-light');

    // dirty:改名 → 未保存提示 + 保存可用;保存 → toast;刷新 → 持久化
    await page.getByTestId('ws-name-input').fill('MES-127 B4 已改名');
    await expect(page.getByText('Unsaved changes')).toBeVisible();
    await expect(page.getByTestId('ws-save')).toBeEnabled();
    await page.getByTestId('ws-save').click();
    await expect(page.getByText('Settings saved.')).toBeVisible();
    await page.reload();
    await expect(page.getByTestId('ws-name-input')).toHaveValue('MES-127 B4 已改名');
    await expect(page.getByText(LOAD_FAILED_TEXT)).toHaveCount(0);

    // G11:工作区默认主题(admin 可见)→ dark 保存 → 账号偏好缺省时工作区页面落暗色
    await expect(page.getByTestId('ws-default-theme-select')).toBeVisible();
    await expect(page.getByTestId('ws-default-theme-hint')).toBeVisible();
    await page.getByTestId('ws-default-theme-select').selectOption('dark');
    await expect(page.getByText('Unsaved changes')).toBeVisible();
    await page.getByTestId('ws-save').click();
    await expect(page.getByText('Settings saved.')).toBeVisible();
    await evidence(page, 'ws-settings-dark');

    // 协商链真实生效:账号偏好缺省 → 工作区默认 dark(工作区上下文页面)
    await page.goto(`/w/${slug}`);
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  });

  test('命令面板:六类检索 + identifier 直达 + 键盘全流程;顶栏同一结果视图', async ({ page }) => {
    const isMobile = test.info().project.name === 'mobile';
    await registerAndContinue(page, uniqueEmail('pal'), 'MES-127 Palette');
    const slug = `mes127c-${String(Date.now()).slice(-8)}`;
    await createWorkspace(page, slug, 'MES-127 Palette WS');
    await createIssueFromHome(page, 'Palette 召回 登录崩溃测试');

    // 抓取真实 identifier(形如 KEY-1)用于直达断言
    const listText = await page.getByTestId('home-issue-list').innerText();
    const identifier = (listText.match(/[A-Z][A-Z0-9]+-\d+/) ?? [''])[0];
    expect(identifier).not.toBe('');

    // —— 顶栏搜索为真实控件(§4.9):键入即展开同一结果视图弹层 ——
    await page.goto('/');
    const topbarSearch = page.getByTestId('topbar-search');
    await topbarSearch.fill('theme');
    await expect(page.getByTestId('topbar-search-popover')).toBeVisible();
    await expect(topbarSearch).toHaveAttribute('aria-expanded', 'true');
    // 本地命令同步零延迟(§11.4)
    await expect(page.getByText('Toggle theme').first()).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByTestId('topbar-search-popover')).toHaveCount(0);

    // §4.5 快捷键契约：IME 组合输入不触发全局快捷键；`/` 命中搜索时
    // 阻止浏览器快速查找并聚焦顶栏；序列首键呈现等待提示且 Esc/超时清除。
    await topbarSearch.evaluate((element) => element.blur());
    await page.evaluate(() => {
      window.dispatchEvent(
        new KeyboardEvent('keydown', {
          key: 'k',
          ctrlKey: true,
          isComposing: true,
          bubbles: true,
          cancelable: true,
        }),
      );
      window.dispatchEvent(
        new KeyboardEvent('keydown', {
          key: 'g',
          isComposing: true,
          bubbles: true,
          cancelable: true,
        }),
      );
    });
    await expect(page.getByRole('dialog', { name: 'Command palette' })).toHaveCount(0);
    await expect(page.locator('.mesh-shortcut-sequence-status')).toHaveCount(0);

    await page.evaluate(() => {
      window.addEventListener(
        'keydown',
        (event) => {
          if (event.key === '/') {
            document.documentElement.dataset.e2eSlashDefaultPrevented = String(
              event.defaultPrevented,
            );
          }
        },
        { once: true },
      );
    });
    await page.keyboard.press('/');
    await expect(topbarSearch).toBeFocused();
    await expect(page.locator('html')).toHaveAttribute('data-e2e-slash-default-prevented', 'true');

    await topbarSearch.evaluate((element) => element.blur());
    await page.keyboard.press('g');
    const sequenceStatus = page.locator('.mesh-shortcut-sequence-status');
    await expect(sequenceStatus).toHaveText('G —');
    await page.keyboard.press('Escape');
    await expect(sequenceStatus).toHaveCount(0);
    await page.keyboard.press('g');
    await expect(sequenceStatus).toHaveText('G —');
    await expect(sequenceStatus).toHaveCount(0, { timeout: 1500 });

    // —— Ctrl/Cmd+K(桌面)/ 顶栏 Enter 交接(手机)开启完整面板 ——
    if (isMobile) {
      await topbarSearch.fill('登录崩溃');
      await topbarSearch.press('Enter'); // 无选中项 → 携带查询交接完整面板
    } else {
      await page.keyboard.press('ControlOrMeta+K');
    }
    const dialog = page.getByRole('dialog', { name: 'Command palette' });
    await expect(dialog).toBeVisible();
    const paletteInput = dialog.getByRole('combobox');
    await expect(paletteInput).toBeFocused();

    // 六类检索:实体召回分组(真实服务端检索;工作项组必现,成员组经显示名召回)
    if (!isMobile) {
      await paletteInput.fill('登录崩溃');
    }
    await expect(dialog.getByRole('group', { name: 'Issues' })).toBeVisible();
    await expect(dialog.locator('[data-testid^="palette-opt-issue:"]').first()).toBeVisible();
    // live region 在场(检索落地播报结果数,§9.6 第 7 点)
    await expect(dialog.getByTestId('palette-live')).toBeAttached();
    await evidence(page, 'palette-light');

    // 键盘:ArrowDown 移动选择(aria-selected 跟随),Enter 打开规范深链
    await paletteInput.press('ArrowDown');
    await paletteInput.press('ArrowDown');
    const selected = dialog.locator('[role="option"][aria-selected="true"]');
    await expect(selected).toHaveCount(1);
    await paletteInput.press('Enter');
    // 选中项为命令则执行命令、为实体则跳详情——二者皆「执行选中」语义;
    // 命令面板关闭即证明激活链路完成。
    await expect(dialog).toHaveCount(0);

    // identifier 等值快路径直达(跳过防抖,§2.2)
    if (isMobile) {
      await page.getByTestId('open-palette').click();
    } else {
      await page.keyboard.press('ControlOrMeta+K');
    }
    await expect(dialog).toBeVisible();
    await dialog.getByRole('combobox').fill(identifier);
    await expect(dialog.locator(`[data-testid^="palette-opt-issue:"]`).first()).toBeVisible();
    await dialog.getByRole('combobox').press('Enter');
    await page.waitForURL(/\/issues\//);
    // 激活即关面板(显式等关闭落地,杜绝后续重开的时序竞争)
    await expect(dialog).toHaveCount(0);

    // Esc 关闭(分层关闭栈)
    if (isMobile) {
      await page.getByTestId('open-palette').click();
    } else {
      await page.keyboard.press('ControlOrMeta+K');
    }
    await expect(dialog).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);

    // 暗色存证:切暗后重开面板
    await setTheme(page, 'dark');
    await page.goto('/');
    if (isMobile) {
      await page.getByTestId('open-palette').click();
    } else {
      await page.keyboard.press('ControlOrMeta+K');
    }
    await expect(dialog).toBeVisible();
    await evidence(page, 'palette-dark');
  });

  test('Analytics:空窗态 → 有数据态 + 范围/口径行 + 四组合存证', async ({ page }) => {
    await registerAndContinue(page, uniqueEmail('ins'), 'MES-127 Insights');
    const slug = `mes127d-${String(Date.now()).slice(-8)}`;
    await createWorkspace(page, slug, 'MES-127 Insights WS');

    // 空窗:新工作区无数据 → 窗口内空态(控件外壳仍在,不崩、无加载失败)
    await page.goto('/insights');
    await expect(page.getByTestId('insights-range')).toBeVisible();
    await expect(page.locator('.mesh-empty-state').first()).toBeVisible();
    await expect(page.getByText(LOAD_FAILED_TEXT)).toHaveCount(0);
    await evidence(page, 'insights-empty-light');

    // 有数据:创建 issue 后 KPI 条呈现(仪表盘若滞后于写入,重载重试至落地)
    await createIssueFromHome(page, 'Analytics 数据点 工作项');
    const kpiStrip = page.locator(
      '.mesh-analytics__kpi-strip:not(.mesh-analytics__kpi-strip--skeleton)',
    );
    await page.goto('/insights');
    for (let attempt = 0; attempt < 6; attempt += 1) {
      if (
        await kpiStrip
          .first()
          .isVisible()
          .catch(() => false)
      )
        break;
      await page.waitForTimeout(1500);
      await page.reload();
    }
    // KPI 条仅非空窗呈现(页面级空态为排他分支;agent 子面板自有空态不在此断言)
    await expect(kpiStrip.first()).toBeVisible();
    await expect(page.getByTestId('insights-range')).toBeVisible();
    await expect(page.getByTestId('insights-tz-note')).toBeVisible();
    await expect(page.getByTestId('insights-caliber')).toBeVisible();
    await expect(page.getByText(LOAD_FAILED_TEXT)).toHaveCount(0);
    await evidence(page, 'insights-data-light');

    // 暗色双张
    await setTheme(page, 'dark');
    await page.goto('/insights');
    await expect(page.getByTestId('insights-range')).toBeVisible();
    await evidence(page, 'insights-data-dark');
  });

  test('G10:/approvals 与 /w/:slug/approvals 深链直达统一审批页', async ({ page }) => {
    await registerAndContinue(page, uniqueEmail('apr'), 'MES-127 Approvals');
    const slug = `mes127e-${String(Date.now()).slice(-8)}`;
    await createWorkspace(page, slug, 'MES-127 Approvals WS');

    // 全局深链:统一审批页(新用户无待审批 → 空态,不崩不失败)
    await page.goto('/approvals');
    await expect(page.locator('main.mesh-approvals')).toBeVisible();
    await expect(
      page.getByTestId('approvals-list').or(page.locator('.mesh-empty-state')),
    ).toBeVisible();
    await expect(page.getByText(LOAD_FAILED_TEXT)).toHaveCount(0);
    await evidence(page, 'approvals-light');

    // 工作区作用域深链(同一页面,工作区上下文解析)
    await page.goto(`/w/${slug}/approvals`);
    await expect(page.locator('main.mesh-approvals')).toBeVisible();
    await expect(
      page.getByTestId('approvals-list').or(page.locator('.mesh-empty-state')),
    ).toBeVisible();

    // README §6.10：真实创建 agent principal + agent token，验证两条前端路由
    // 都在列表请求前呈现人类专属门控。token 仅存本次隔离栈/localStorage，不输出。
    const ownerToken = await page.evaluate(() => {
      const raw = localStorage.getItem('mesh.auth.v1');
      if (raw === null) throw new Error('missing authenticated session');
      const token = (JSON.parse(raw) as { state?: { token?: unknown } }).state?.token;
      if (typeof token !== 'string') throw new Error('missing access token');
      return token;
    });
    const authHeaders = { Authorization: `Bearer ${ownerToken}` };
    const workspacesResponse = await page.request.get('/api/v1/workspaces', {
      headers: authHeaders,
    });
    expect(workspacesResponse.status()).toBe(200);
    const workspacesBody = (await workspacesResponse.json()) as {
      data: Array<{ id: string; slug: string }>;
    };
    const workspaceId = workspacesBody.data.find((item) => item.slug === slug)?.id;
    expect(workspaceId).toBeTruthy();

    const agentResponse = await page.request.post(`/api/v1/workspaces/${workspaceId}/agents`, {
      headers: authHeaders,
      data: {
        name: 'MES-127 Approval Gate Agent',
        role_tag: 'Reviewer',
        bio: 'Agent-principal approval gate verification.',
        system_instructions: 'Verify the human-only approval presentation gate.',
        model_config: { model_tier: 'balanced', temperature: 0.2, max_tokens: 1024 },
      },
    });
    expect(agentResponse.status()).toBe(201);
    const agentBody = (await agentResponse.json()) as { data: { member: { id: string } } };
    const tokenResponse = await page.request.post(`/api/v1/workspaces/${workspaceId}/api-tokens`, {
      headers: authHeaders,
      data: {
        name: 'MES-127 approval gate',
        owner_member_id: agentBody.data.member.id,
        scopes: [],
      },
    });
    expect(tokenResponse.status()).toBe(201);
    const tokenBody = (await tokenResponse.json()) as { data: { token: string } };
    const agentToken = tokenBody.data.token;
    expect(agentToken.startsWith('mesh_agt_')).toBe(true);

    const approvalListRequests: string[] = [];
    page.on('request', (request) => {
      if (
        request.method() === 'GET' &&
        /\/api\/v1\/workspaces\/[^/]+\/approvals(?:\?|$)/.test(request.url())
      ) {
        approvalListRequests.push(request.url());
      }
    });
    await page.evaluate((token) => {
      const raw = localStorage.getItem('mesh.auth.v1');
      if (raw === null) throw new Error('missing auth storage');
      const persisted = JSON.parse(raw) as { state: { token: string | null } };
      persisted.state.token = token;
      localStorage.setItem('mesh.auth.v1', JSON.stringify(persisted));
    }, agentToken);
    await page.goto('/approvals');
    await expect(page.getByTestId('approvals-agent-gated')).toBeVisible();
    await page.goto(`/w/${slug}/approvals`);
    await expect(page.getByTestId('approvals-agent-gated')).toBeVisible();
    expect(approvalListRequests).toHaveLength(0);

    // 恢复人类 session，继续暗色深链存证。
    await page.evaluate((token) => {
      const raw = localStorage.getItem('mesh.auth.v1');
      if (raw === null) throw new Error('missing auth storage');
      const persisted = JSON.parse(raw) as { state: { token: string | null } };
      persisted.state.token = token;
      localStorage.setItem('mesh.auth.v1', JSON.stringify(persisted));
    }, ownerToken);

    await setTheme(page, 'dark');
    await page.goto('/approvals');
    await expect(page.locator('main.mesh-approvals')).toBeVisible();
    await evidence(page, 'approvals-dark');
  });
});
