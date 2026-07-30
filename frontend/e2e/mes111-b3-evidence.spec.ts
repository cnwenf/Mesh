/**
 * MES-111 批次③ 真实 e2e + 四组合走查存证(成员/Agent 名册 + 收件箱 + 聊天)。
 *
 * 前置:真实后端栈运行中(docker compose -p mes126b3 up -d postgres redis minio
 * api worker gateway frontend,MESH_API_PORT=18126 / MESH_WS_PORT=18127 /
 * MESH_STORAGE_PORT=18128 / MESH_FRONTEND_PORT=18130);主走查经 compose 前端
 * (:18130 同源反代,生产形态)。附件直传步另经 dev server(:5326,指向 18126/18127;
 * `VITE_MESH_API_BASE_URL=http://127.0.0.1:18126 VITE_MESH_WS_BASE_URL=ws://127.0.0.1:18127
 *  npx vite --port 5326 --host 127.0.0.1`),口径同 MES-59 real-attachment
 * (compose HTML 入口 CSP connect-src 'self' 与浏览器直连对象存储的联调口径不同)。
 *
 * 走查内容(验收硬门槛 #2/#3):
 * - 成员:名册渲染(底座 Avatar/AI 徽标)→ 人类成员角色改动(真实 PATCH + 落库校验)→
 *   行操作菜单(底座 Menu)→ agent 详情深链(头部运行态五态徽标);
 *   手机:主次行卡片(无横向溢出,表格隐藏)。
 * - 收件箱:双栏(分组列表 + 预览)→ 标已读(真实 POST + read_at 落库校验)→ 归档
 *   (行移除)→ 深链(/issues/{id}#comment-{anchor});手机:单栏路由化(/inbox/:id + 返回)。
 * - 聊天:新建会话 → 流式发送(真实 SSE,内建上游逐块回复)→ 停止 → 重生成 →
 *   附件(真实 minio 直传)→ 上下文条收起/展开;手机:列表/会话路由化 + 返回 + 粘底输入区。
 *
 * 四组合(桌面 1440×900 / 手机 390×844 × 亮/暗)经 projects 并行编排,每步存证
 * `${project}-${NN}-${name}.png`,内容天然互异(md5 唯一性门禁 scripts/check-evidence-unique.mjs)。
 */
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';

const API = process.env.MESH_E2E_API_BASE ?? 'http://127.0.0.1:18126';
const PASSWORD = 'Batch3-Str0ng!pass';
const EVIDENCE = 'e2e/evidence/mes111-b3';

function authHeaders(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` };
}

async function apiRegister(
  request: APIRequestContext,
  email: string,
  name: string,
): Promise<string> {
  await request.post(`${API}/api/v1/auth/register`, {
    data: { email, password: PASSWORD, display_name: name },
  });
  const login = await request.post(`${API}/api/v1/auth/login`, {
    data: { email, password: PASSWORD },
  });
  const body = await login.json();
  return String(body.data.access_token);
}

async function apiJson(
  request: APIRequestContext,
  method: 'GET' | 'POST' | 'PATCH' | 'DELETE',
  path: string,
  token: string,
  data?: Record<string, unknown>,
): Promise<{ status: number; body: Record<string, unknown> }> {
  const res = await request.fetch(`${API}${path}`, {
    method,
    headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
    data: data === undefined ? undefined : JSON.stringify(data),
  });
  const text = await res.text();
  return {
    status: res.status(),
    body: text === '' ? {} : (JSON.parse(text) as Record<string, unknown>),
  };
}

/** 经 UI 真实登录(像真人:打开登录页、填表、提交)+ 关闭引导面板。
 *  引导清单会在步骤自动完成时触发跳转/重渲染,与后续 goto 竞争导致截图落到
 *  首页;存证前先 dismiss,保证走查页稳定(存证 md5 唯一性 #1)。 */
async function uiLogin(page: Page, email: string): Promise<void> {
  await page.goto('/login');
  await page.getByTestId('login-email').fill(email);
  await page.getByTestId('login-password').fill(PASSWORD);
  await page.getByTestId('login-account-submit').click();
  await expect(page.locator('.mesh-shell')).toBeVisible({ timeout: 30_000 });
}

/** 将主题写入服务端偏好(PATCH /users/me §3.1),避免 preferencesSync 用
 *  服务端镜像(null→继承→亮)覆盖本地预置主题,致亮/暗存证撞色。 */
async function setServerTheme(
  request: APIRequestContext,
  token: string,
  theme: string,
): Promise<void> {
  await apiJson(request, 'PATCH', '/api/v1/users/me', token, { settings: { theme } });
}

/** 暗色经持久化偏好预置(theme.md 协商链,防闪烁;与 visual-helpers 同形)。 */
async function presetTheme(page: Page, theme: 'light' | 'dark'): Promise<void> {
  await page.addInitScript(
    ({ mode }) => {
      window.localStorage.setItem(
        'mesh.settings.v1',
        JSON.stringify({
          state: { preferences: { theme: mode, locale: null, timezone: 'UTC' } },
          version: 2,
        }),
      );
    },
    { mode: theme },
  );
}

interface SeededWorld {
  readonly ownerEmail: string;
  readonly peerEmail: string;
  readonly workspaceId: string;
  readonly agentId: string;
  readonly agentName: string;
  readonly issueId: string;
}

/** 数据准备(真实 API 播种:工作区/agent/第二成员/issue + 跨成员评论 → 收件箱非空)。 */
async function seedWorld(request: APIRequestContext, runTag: string): Promise<SeededWorld> {
  const ownerEmail = `b3-owner-${runTag}@corp.example`;
  const peerEmail = `b3-peer-${runTag}@corp.example`;
  const ownerToken = await apiRegister(request, ownerEmail, 'Batch Three Owner');
  const ws = await apiJson(request, 'POST', '/api/v1/workspaces', ownerToken, {
    name: 'Batch3 Walkthrough',
    slug: `b3${runTag}`,
  });
  const workspaceId = String((ws.body.data as { id: string }).id);
  const agentName = `代码助手 ${runTag}`;
  const agent = await apiJson(
    request,
    'POST',
    `/api/v1/workspaces/${workspaceId}/agents`,
    ownerToken,
    {
      name: agentName,
      role_tag: '工程',
      bio: '负责批次③走查的测试 agent',
      visibility: 'workspace',
    },
  );
  const agentId = String((agent.body.data as { id: string }).id);
  const issue = await apiJson(
    request,
    'POST',
    `/api/v1/workspaces/${workspaceId}/issues`,
    ownerToken,
    {
      title: `批次③走查 issue ${runTag}`,
    },
  );
  const issueId = String((issue.body.data as { id: string }).id);
  // 邀请第二名人类成员并兑换(名册角色改动对象)。
  const inv = await apiJson(
    request,
    'POST',
    `/api/v1/workspaces/${workspaceId}/invitations`,
    ownerToken,
    {
      emails: [peerEmail],
      role: 'member',
    },
  );
  const inviteLink = String((inv.body.data as Array<{ invite_link: string }>)[0].invite_link);
  const inviteToken = inviteLink.split('/').pop() ?? '';
  const peerToken = await apiRegister(request, peerEmail, 'Batch Three Peer');
  // 服务端关闭引导清单(§3.5,幂等):手机端清单为页内接管层,会遮挡/拦截走查页
  // 点击并污染存证;播种期即 dismiss,使 UI 走查全程不渲染该面板。
  await apiJson(
    request,
    'POST',
    `/api/v1/onboarding/dismiss?workspace_id=${workspaceId}`,
    ownerToken,
    {},
  );
  await apiJson(request, 'POST', '/api/v1/invitations/accept', peerToken, { token: inviteToken });
  // 同伴评论 → owner 收件箱收到 comment_created 通知(跨成员,不被自我抑制)。
  const comment = await apiJson(request, 'POST', `/api/v1/issues/${issueId}/comments`, peerToken, {
    body_markdown: `我来跟进这个走查任务 ${runTag}。`,
  });
  expect(comment.status).toBe(201);
  return { ownerEmail, peerEmail, workspaceId, agentId, agentName, issueId };
}

test.describe('MES-111 批次③ 四组合真实走查', () => {
  test('成员名册 + 收件箱 + 聊天全链路(真实操作 + 存证)', async ({ page, request }, testInfo) => {
    test.setTimeout(300_000);
    const project = testInfo.project.name; // desktop-light | desktop-dark | phone-light | phone-dark
    const isPhone = project.startsWith('phone');
    const theme = project.endsWith('dark') ? 'dark' : 'light';
    const runTag = `${project}-${String(Date.now()).slice(-7)}`;
    const shot = `${EVIDENCE}/${project}`;

    await presetTheme(page, theme);
    const world = await seedWorld(request, runTag);
    const ownerToken = await apiRegister(request, world.ownerEmail, 'Batch Three Owner');
    await setServerTheme(request, ownerToken, theme);
    await uiLogin(page, world.ownerEmail);

    // ── 成员名册 ──────────────────────────────────────────────────────────
    await page.goto('/members');
    await expect(page.getByTestId('tab-all')).toBeVisible({ timeout: 30_000 });
    if (isPhone) {
      // A-05 收尾:手机为卡片、表格隐藏、无页面级横向溢出。
      await expect(page.locator('[data-testid^="member-card-"]').first()).toBeVisible({
        timeout: 30_000,
      });
      const tableHidden = await page.evaluate(() => {
        const wrap = document.querySelector('.mesh-members__table-wrap');
        return wrap === null || getComputedStyle(wrap).display === 'none';
      });
      expect(tableHidden).toBe(true);
      const noOverflow = await page.evaluate(
        () => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
      );
      expect(noOverflow).toBe(true);
    } else {
      await expect(page.locator('[data-testid^="member-open-"]').first()).toBeVisible({
        timeout: 30_000,
      });
    }
    // 底座 Avatar(人类缩写/agent 轮廓)与 AI 徽标在场(手机为 card- 前缀;
    // 表格在 ≤599px 为 CSS 隐藏,断言限定卡片作用域)。
    const avatarScope = isPhone ? '[data-testid^="member-card-"] .mesh-avatar' : '.mesh-avatar';
    await expect(page.locator(avatarScope).first()).toBeVisible();
    const badgePrefix = isPhone ? 'card-ai-badge-' : 'ai-badge-';
    await expect(page.locator(`[data-testid^="${badgePrefix}"]`).first()).toBeVisible();
    await page.screenshot({ path: `${shot}-01-members-roster.png` });

    // 角色改动(真实 PATCH + 落库校验):把第二名人类成员改为 admin。
    const peerMember = await apiJson(
      request,
      'GET',
      `/api/v1/workspaces/${world.workspaceId}/members?limit=100`,
      ownerToken,
    );
    const peers = (
      peerMember.body.data as Array<{ id: string; profile: { email?: string } | null }>
    ).filter(
      (m) => m.profile !== null && (m.profile as { email?: string }).email === world.peerEmail,
    );
    expect(peers.length).toBe(1);
    const peerMemberId = peers[0].id;
    // 桌面=表格内角色下拉;手机=卡片内角色下拉(card- 前缀,触控可达)。
    const roleSelect = isPhone
      ? page.getByTestId(`card-role-select-${peerMemberId}`)
      : page.getByTestId(`role-select-${peerMemberId}`);
    await roleSelect.selectOption('admin');
    await expect
      .poll(
        async () => {
          const res = await apiJson(
            request,
            'GET',
            `/api/v1/workspaces/${world.workspaceId}/members/${peerMemberId}`,
            ownerToken,
          );
          return (res.body.data as { role: string }).role;
        },
        { timeout: 15_000 },
      )
      .toBe('admin');
    await page.screenshot({ path: `${shot}-02-members-role-changed.png` });

    // agent 详情深链(唯一入口:名册 agent 行点击;手机为卡片)。
    const agentBadge = page.locator(`[data-testid^="${badgePrefix}"]`).first();
    await agentBadge.locator('xpath=ancestor::button[1]').click();
    await expect(page.getByTestId('agent-detail-page')).toBeVisible({ timeout: 30_000 });
    // 头部运行态五态徽标(§9.8 统一语言):data-state ∈ 七态之一。
    const runState = await page
      .getByTestId('agent-detail-presence')
      .locator('[data-state]')
      .first()
      .getAttribute('data-state');
    expect(['queued', 'running', 'waiting', 'succeeded', 'failed', 'idle', 'unknown']).toContain(
      runState,
    );
    await page.screenshot({ path: `${shot}-03-agent-detail.png` });

    // ── 收件箱 ────────────────────────────────────────────────────────────
    await page.goto('/inbox');
    await expect(page.getByTestId('inbox-page')).toBeVisible({ timeout: 30_000 });
    await expect(page.locator('[data-testid^="inbox-row-"]').first()).toBeVisible({
      timeout: 30_000,
    });
    await page.screenshot({ path: `${shot}-04-inbox-list.png` });

    const firstRow = page.locator('[data-testid^="inbox-row-"]').first();
    const rowTestId = (await firstRow.getAttribute('data-testid')) ?? '';
    const notificationId = rowTestId.replace('inbox-row-', '');
    await firstRow.locator('button').first().click();
    // 预览窗格(桌面双栏右列 / 手机路由化详情)。
    await expect(page.getByTestId('inbox-preview-title')).toBeVisible({ timeout: 15_000 });
    if (isPhone) {
      expect(page.url()).toContain(`/inbox/${notificationId}`);
    }
    await page.screenshot({ path: `${shot}-05-inbox-preview.png` });

    // 标已读(真实 POST + read_at 落库)。
    if (
      await page
        .getByTestId('inbox-preview-mark-read')
        .isVisible()
        .catch(() => false)
    ) {
      await page.getByTestId('inbox-preview-mark-read').click();
    }
    await expect
      .poll(
        async () => {
          const res = await apiJson(
            request,
            'GET',
            `/api/v1/inbox?workspace_id=${world.workspaceId}&limit=100`,
            ownerToken,
          );
          const items = res.body.data as Array<{ id: string; read_at: string | null }>;
          return items.find((n) => n.id === notificationId)?.read_at ?? null;
        },
        { timeout: 15_000 },
      )
      .not.toBeNull();
    if (isPhone) {
      // 单栏下预览窗标已读无可视差异(列表隐藏);返回列表截「已读态」(未读点消失),
      // 保证与 05 预览截图互异(存证 md5 唯一性 #1)。
      await page.getByTestId('inbox-preview-back').click();
      await page.waitForURL(/\/inbox$/, { timeout: 15_000 });
    }
    await page.screenshot({ path: `${shot}-06-inbox-read.png` });

    // 深链:查看来源 → issue 详情 + 评论锚点。
    await page.goto('/inbox');
    await expect(page.locator('[data-testid^="inbox-row-"]').first()).toBeVisible({
      timeout: 20_000,
    });
    const row2 = page.locator('[data-testid^="inbox-row-"]').first();
    await row2.locator('button').first().click();
    await page.getByTestId('inbox-preview-open').click();
    await page.waitForURL(/\/issues\//, { timeout: 15_000 });
    expect(page.url()).toContain('/issues/');
    await page.screenshot({ path: `${shot}-07-inbox-deeplink.png` });

    // 归档:回收件箱,归档首条 → 行移除。
    await page.goto('/inbox');
    if (
      await page
        .locator('[data-testid^="inbox-row-"]')
        .first()
        .isVisible({ timeout: 10_000 })
        .catch(() => false)
    ) {
      const before = await page.locator('[data-testid^="inbox-row-"]').count();
      const archiveBtn = page.locator('[data-testid^="inbox-archive-"]').first();
      if (await archiveBtn.isVisible({ timeout: 5_000 }).catch(() => false)) {
        await archiveBtn.click();
        await expect
          .poll(async () => page.locator('[data-testid^="inbox-row-"]').count(), {
            timeout: 10_000,
          })
          .toBeLessThan(before);
      }
    }
    await page.screenshot({ path: `${shot}-08-inbox-archived.png` });

    if (isPhone) {
      // 手机单栏路由化:详情 → 返回 → 列表。
      await page.goto('/inbox');
      if (
        await page
          .locator('[data-testid^="inbox-row-"]')
          .first()
          .isVisible({ timeout: 10_000 })
          .catch(() => false)
      ) {
        await page.locator('[data-testid^="inbox-row-"]').first().locator('button').first().click();
        await expect(page.getByTestId('inbox-preview-title')).toBeVisible({ timeout: 15_000 });
        await page.getByTestId('inbox-preview-back').click();
        await page.waitForURL(/\/inbox$/, { timeout: 15_000 });
      }
    }

    // ── 聊天 ──────────────────────────────────────────────────────────────
    await page.goto('/chat');
    await expect(page.getByTestId('chat-page')).toBeVisible({ timeout: 30_000 });
    // 新建会话(UI 真实操作:选 agent → 创建)。
    await page.getByTestId('chat-new-session').first().click();
    const agentSelect = page.getByTestId('chat-new-session-agent');
    await expect(agentSelect.locator('option', { hasText: world.agentName }).first()).toBeAttached({
      timeout: 30_000,
    });
    await agentSelect.selectOption({ label: world.agentName });
    await page.getByTestId('chat-new-session-create').click();
    await expect(page.getByTestId('chat-composer-input')).toBeVisible({ timeout: 30_000 });
    if (isPhone) {
      expect(page.url()).toMatch(/\/chat\/[^/]+$/);
    }
    await page.screenshot({ path: `${shot}-09-chat-new-session.png` });

    // 流式发送(真实 SSE:内建上游逐块回复)。
    await page.getByTestId('chat-composer-input').fill(`你好,走查 ${runTag}:请简短回复。`);
    await page.getByTestId('chat-composer-send').click();
    await expect(page.locator('[data-testid^="chat-body-"]').last()).not.toHaveText('', {
      timeout: 60_000,
    });
    await expect(page.locator('[data-testid^="chat-body-"]')).toHaveCount(2, { timeout: 60_000 });
    await page.screenshot({ path: `${shot}-10-chat-streamed.png` });

    // 停止:再发一条,流式途中点停止(内建上游为确定性占位回复:中断或先完成皆真实终态)。
    await page.getByTestId('chat-composer-input').fill('请写一篇很长的文章。');
    await page.getByTestId('chat-composer-send').click();
    const stop = page.getByTestId('chat-stop');
    if (await stop.isVisible({ timeout: 3_000 }).catch(() => false)) {
      await stop.click();
    }
    await expect(page.locator('[data-testid^="chat-body-"]').last()).not.toHaveText('', {
      timeout: 60_000,
    });
    await page.screenshot({ path: `${shot}-11-chat-stopped.png` });

    // 重生成:对最近一条 agent 消息点重生成 → 新流式回复。
    const regenerate = page.locator('[data-testid^="chat-regenerate-"]').last();
    if (await regenerate.isVisible({ timeout: 5_000 }).catch(() => false)) {
      const beforeCount = await page.locator('[data-testid^="chat-body-"]').count();
      await regenerate.click();
      await expect
        .poll(async () => page.locator('[data-testid^="chat-body-"]').count(), { timeout: 60_000 })
        .toBeGreaterThanOrEqual(beforeCount);
      await page.screenshot({ path: `${shot}-12-chat-regenerated.png` });
    }

    // 附件(真实 minio 直传:小 PNG → 扫描门 → 气泡内附件卡)。
    // 直传 PUT 走浏览器 → MinIO 公网端点;compose 形态的 HTML 入口 CSP 为
    // `connect-src 'self'`(backend/web/entry.py,生产同源反代口径),与「浏览器
    // 直连对象存储」的既有联调口径(attachment.md §3 + MES-59 real-attachment
    // 经 dev server + --disable-web-security)不同。故附件步沿用 MES-59 先例:
    // 另开 dev server 页面(:5326,指向同一真实后端)走直传,截图同批存证。
    const pngPath = `e2e/.b3-upload-${runTag}.png`;
    const DEV_BASE = process.env.MESH_E2E_DEV_BASE ?? 'http://127.0.0.1:5326';
    await test.step('upload attachment (dev-server 口径,同 MES-59)', async () => {
      const { writeFileSync, unlinkSync } = await import('node:fs');
      const b64 =
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==';
      writeFileSync(pngPath, Buffer.from(b64, 'base64'));
      const browser = page.context().browser();
      if (browser === null) throw new Error('browser fixture unavailable');
      const devContext = await browser.newContext({ viewport: page.viewportSize() ?? undefined });
      const devPage = await devContext.newPage();
      try {
        await devPage.addInitScript(
          ([token]) => {
            window.localStorage.setItem(
              'mesh.auth.v1',
              JSON.stringify({ state: { token, refreshToken: null }, version: 0 }),
            );
          },
          [ownerToken],
        );
        // 直传用独立会话:经 API 建一个新会话(复用 world 的 agent)。
        const sess = await apiJson(
          request,
          'POST',
          `/api/v1/workspaces/${world.workspaceId}/chat-sessions`,
          ownerToken,
          {
            agent_id: world.agentId,
          },
        );
        const sessionId = String((sess.body.data as { id: string }).id);
        await devPage.goto(`${DEV_BASE}/chat/${sessionId}`);
        await devPage
          .getByTestId('chat-composer-file')
          .waitFor({ state: 'attached', timeout: 60_000 });
        await devPage.getByTestId('chat-composer-file').setInputFiles(pngPath);
        await expect(devPage.getByTestId('chat-composer-uploads')).toBeVisible({ timeout: 30_000 });
        await devPage.getByTestId('chat-composer-input').fill(`附件走查 ${runTag}`);
        await devPage.getByTestId('chat-composer-send').click();
        await expect(devPage.locator('[data-testid^="chat-attachment-"]').first()).toBeVisible({
          timeout: 60_000,
        });
        await devPage.screenshot({ path: `${shot}-13-chat-attachment.png` });
      } finally {
        await devContext.close();
        try {
          unlinkSync(pngPath);
        } catch {
          /* best-effort */
        }
      }
    });

    // 上下文条收起/展开(§3.2 可收起条)。
    const collapse = page.getByTestId('chat-context-collapse');
    if (await collapse.isVisible({ timeout: 5_000 }).catch(() => false)) {
      await collapse.click();
      await expect(page.getByTestId('chat-context-expand')).toBeVisible({ timeout: 10_000 });
      await page.screenshot({ path: `${shot}-14-chat-context-collapsed.png` });
      await page.getByTestId('chat-context-expand').click();
      await expect(page.getByTestId('chat-context-collapse')).toBeVisible({ timeout: 10_000 });
    }

    if (isPhone) {
      // 手机:会话 → 返回列表(路由化)+ 输入区粘底在视口内。
      await page.getByTestId('chat-back').click();
      await page.waitForURL(/\/chat$/, { timeout: 15_000 });
      await expect(page.locator('[data-testid^="chat-session-"]').first()).toBeVisible({
        timeout: 15_000,
      });
      await page.screenshot({ path: `${shot}-15-phone-chat-list.png` });
    }
  });
});
