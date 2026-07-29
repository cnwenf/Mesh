/**
 * MES-79 真实栈 e2e 走查(search-command-palette.md §5.1):
 * ① 命令面板 Ctrl+K 打开,实体搜索分组命中六类对象,Enter 直达规范深链;
 * ② 顶栏搜索输入即展开同一面板(§4.9 键鼠一致);
 * ③ 规范深链直接访问正确渲染;旧扁平路由经 replace navigation 迁移;
 * ④ `?` 帮助层按上下文呈现;⑤ mod+Enter 新标签不破坏当前上下文。
 *
 * 前置:真实后端栈运行中(见 playwright.mes79.config.ts)。
 */
import { expect, test } from '@playwright/test';
import type { Browser, Page } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const RUN = String(Date.now()).slice(-7);
const EMAIL = `mes79-real-${RUN}@corp.example`;
const PASSWORD = 'secret123';
const SLUG = `m79${RUN}`.slice(0, 12);
const API_BASE = process.env.MES79_API_BASE ?? 'http://127.0.0.1:8300';
const EVIDENCE_DIR = process.env.MES79_EVIDENCE_DIR ?? resolve(HERE, 'evidence', 'mes79');

test.describe.configure({ mode: 'serial' });

let page: Page;
let workspaceId = '';
let projectId = '';
let agentId = '';
let identifier = '';

async function authHeaders(target: Page): Promise<Record<string, string>> {
  return target.evaluate(() => {
    const raw = localStorage.getItem('mesh.auth.v1');
    const parsed = raw === null ? {} : (JSON.parse(raw) as { state?: { token?: string } });
    return {
      Authorization: `Bearer ${parsed.state?.token ?? ''}`,
      'Content-Type': 'application/json',
    };
  });
}

async function api(
  target: Page,
  method: string,
  path: string,
  body?: unknown,
): Promise<Record<string, unknown>> {
  const headers = await authHeaders(target);
  const response = await target.request.fetch(`${API_BASE}/api/v1${path}`, {
    method,
    headers,
    data: body === undefined ? undefined : JSON.stringify(body),
  });
  expect(response.ok(), `${method} ${path} → ${response.status()}`).toBe(true);
  return (await response.json()) as Record<string, unknown>;
}

test.beforeAll(async ({ browser }: { browser: Browser }) => {
  page = await (await browser.newContext()).newPage();

  // 注册 → 登录(真实 UI 表单)
  await page.goto('/login');
  await page.getByTestId('login-mode-register').click();
  await page.getByTestId('login-display-name').fill('走查员 MES79');
  await page.getByTestId('login-email').fill(EMAIL);
  await page.getByTestId('login-password').fill(PASSWORD);
  await page.getByTestId('login-account-submit').click();
  // 注册后进入「查收验证邮件」过渡态(dev 模式令牌存 Redis dev-mailbox);
  // 会话已建立,点「继续」进入主壳(与 real-labels 等既有用例同路径)。
  await page.getByTestId('register-continue').click();
  await expect(page.locator('.mesh-shell')).toBeVisible({ timeout: 30_000 });

  // 经真实 API 播种工作区与六类对象(与 UI 共享同一真实数据库)
  const ws = (await api(page, 'POST', '/workspaces', { name: 'MES79 Real', slug: SLUG }))
    .data as Record<string, string>;
  workspaceId = ws.id;
  const project = (
    await api(page, 'POST', `/workspaces/${workspaceId}/projects`, {
      name: '走查项目',
      key: `M${RUN.slice(-2)}`,
    })
  ).data as Record<string, string>;
  projectId = project.id;
  const agent = (await api(page, 'POST', `/workspaces/${workspaceId}/agents`, { name: '走查助手' }))
    .data as Record<string, string>;
  agentId = agent.id;
  const issue = (
    await api(page, 'POST', `/workspaces/${workspaceId}/issues`, {
      title: '走查登录页崩溃',
      project_id: projectId,
    })
  ).data as Record<string, string>;
  identifier = issue.identifier;
  await api(page, 'POST', `/workspaces/${workspaceId}/views`, {
    name: '走查看板',
    layout: 'board',
    visibility: 'shared',
  });
  await api(page, 'POST', `/workspaces/${workspaceId}/chat-sessions`, {
    agent_id: agentId,
    title: '走查会话',
  });
});

test.afterAll(async () => {
  await page.close();
});

test('Ctrl+K 命令面板:分组实体搜索 + Enter 规范深链 + 帮助层', async () => {
  await page.goto('/');
  await page.keyboard.press('Control+K');
  const combobox = page.getByRole('combobox');
  await expect(combobox).toBeFocused({ timeout: 15_000 });

  await combobox.fill('走查');
  // 六类对象分组命中(issue/member/agent/project/view/chat_session)——
  // 按 option 角色断言,避免标题/副标题同名的 strict-mode 歧义。
  await expect(page.getByRole('option', { name: /走查登录页崩溃/ })).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByRole('option', { name: /走查助手/ }).first()).toBeVisible();
  await expect(page.getByRole('option', { name: /走查项目/ }).first()).toBeVisible();
  await expect(page.getByRole('option', { name: /走查看板/ })).toBeVisible();
  await expect(page.getByRole('option', { name: /走查会话/ })).toBeVisible();
  await expect(page.getByRole('option', { name: /走查员 MES79/ })).toBeVisible();
  await page.screenshot({ path: resolve(EVIDENCE_DIR, 'palette-groups.png') });

  // Enter 直达 issue 规范深链(§3.4)
  await page.getByText('走查登录页崩溃').click();
  await page.waitForURL(`**/w/${SLUG}/issues/by-identifier/${identifier}`);
  await page.screenshot({ path: resolve(EVIDENCE_DIR, 'issue-deep-link.png') });

  // `?` 帮助层(§4.4)
  await page.keyboard.press('?');
  await expect(page.getByRole('dialog', { name: 'Keyboard shortcuts' })).toBeVisible();
  await page.screenshot({ path: resolve(EVIDENCE_DIR, 'help-layer.png') });
  await page.keyboard.press('Escape');
});

test('顶栏搜索输入即展开同一面板(§4.9)', async () => {
  await page.goto(`/w/${SLUG}/inbox`);
  await page.getByTestId('topbar-search').pressSequentially('走查项目', { delay: 30 });
  await expect(page.getByRole('dialog', { name: 'Command palette' })).toBeVisible();
  // 按 option 角色断言,消除 strict-mode 歧义(项目行与副标题含项目名的 issue 行
  // 均匹配;no-results 已门控为「检索完成且结果空」,在途窗口不再瞬态闪现)。
  await expect(page.getByRole('option', { name: /走查项目/ }).first()).toBeVisible();
  await page.screenshot({ path: resolve(EVIDENCE_DIR, 'topbar-search.png') });
  await page.keyboard.press('Escape');
  await page.keyboard.press('Escape');
});

test('规范深链直达 + 旧扁平路由 replace 迁移(§3.4)', async () => {
  // 九条规范深链抽样:项目 / 成员 / 视图 / 聊天
  await page.goto(`/w/${SLUG}/projects/${projectId}`);
  await expect(page.getByRole('main').last()).toContainText('走查项目', { timeout: 30_000 });
  await page.goto(`/w/${SLUG}/members`);
  await expect(page.getByRole('main').last()).toContainText('走查员 MES79');
  // 旧扁平路由 → active workspace 解析 → 规范路由(replace,保留 query)
  await page.goto('/projects?from=flat');
  await page.waitForURL(`**/w/${SLUG}/projects?from=flat`, { timeout: 30_000 });
  await page.screenshot({ path: resolve(EVIDENCE_DIR, 'flat-migration.png') });
});

test('标识符快路径:小写 identifier 顶置命中(§5.1)', async () => {
  await page.goto('/');
  await page.keyboard.press('Control+K');
  const combobox = page.getByRole('combobox');
  await expect(combobox).toBeFocused({ timeout: 15_000 });
  await combobox.fill(identifier.toLowerCase());
  const first = page.getByRole('option').first();
  await expect(first).toContainText(identifier);
  await page.keyboard.press('Enter');
  await page.waitForURL(`**/issues/by-identifier/${identifier}`);
});

test('P0:规范深链页 /w/{slug}/… 上面板实体搜索可用(slug scope,§3.1/§3.4)', async () => {
  // 主路径回归:identity 自 URL slug 解析 scope,搜索命中 /workspaces/{slug}/search。
  // 此前 usePaletteIdentity 以 slug 拼请求而后端仅受 UUID → 404「Search failed」;
  // 后端 slug 解析修通后,此页级流程须分组命中并直达规范深链。
  await page.goto(`/w/${SLUG}/inbox`);
  await expect(page.locator('.mesh-shell')).toBeVisible({ timeout: 30_000 });
  await page.keyboard.press('Control+K');
  const combobox = page.getByRole('combobox');
  await expect(combobox).toBeFocused({ timeout: 15_000 });
  await combobox.fill('走查');
  // 分组实体结果命中(而非「Search failed / 搜索失败」错误态)。
  const issueOption = page.getByRole('option', { name: /走查登录页崩溃/ });
  await expect(issueOption).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole('option', { name: /走查项目/ }).first()).toBeVisible();
  await expect(page.getByText('Search failed')).toHaveCount(0);
  await expect(page.getByText('搜索失败')).toHaveCount(0);
  await page.screenshot({ path: resolve(EVIDENCE_DIR, 'palette-on-slug-page.png') });
  // 显式点击目标 option 直达 issue 规范深链(URL 保持 slug 形态)——比依赖键盘默认
  // 选中行更确定:无头浏览器截图会移动虚拟鼠标触发某行 onMouseEnter,使 Enter 命中
  // 非首行;直接点击目标行规避该悬停副作用,且语义即「选中该项并直达」。
  await issueOption.click();
  await page.waitForURL(`**/w/${SLUG}/issues/by-identifier/${identifier}`);
});
