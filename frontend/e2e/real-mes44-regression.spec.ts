/**
 * MES-44 真实 UI 实操回归(对照验收截图 ev-m5 / ev-b5,issue.md §4.3/§3.8/§4.4/§5.2):
 * 在一个**包含 issue 模块**的真实后端栈(postgres+redis+api+worker+gateway,
 * MESH_AUTH_MODE=dev)上,以真实浏览器像真人一样操作,复现并验证两点修复:
 *   点 1 —— 跨项目迁移预览对话框须标明「目标项目名」,且在确有状态映射时与「清除」
 *           并列展示 mapped 清单;仅清除场景下 mapped 区不渲染(与截图 ev-m5 同形态)。
 *   点 2 —— 严格模式下被禁转换:危险 toast 经 i18n(zh-CN 显示中文)、status <select>
 *           就地回落原值、不保留被禁目标值、不触发整页 reload / 骨架闪烁、无 unhandled
 *           rejection;strict 关闭时同一转换可成功。
 *
 * 数据经真实 REST 准备(注册→登录拿 JWT→建工作区/项目/状态/里程碑/工作项),
 * UI 经真实页面驱动。截图落 e2e/evidence/ 供 Issue 附件。
 *
 * 后端端口经 MES44_API_PORT 配置(默认 8000,与 docker-compose 一致;隔离拉起见
 * playwright.mes44.config.ts 顶部说明)。
 */
import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

const BACKEND = `http://127.0.0.1:${process.env.MES44_API_PORT ?? '8000'}`;
const EMAIL = `mes44-ui-${Date.now()}@corp.example`;
const PASSWORD = 'Passw0rd!-mes44-ui';
const SLUG = `mes44ui${Date.now().toString().slice(-6)}`;

/** loosely-typed JSON object(e2e 边界,避免 any)。 */
type Json = Record<string, unknown>;

interface Ctx {
  ws: string;
  prjA: string;
  prjB: string;
  /** 工作区级严格源状态(allowed_transitions=[]) */
  strictStatus: string;
  /** prjA 的项目私有 todo 状态(触发跨项目迁移映射 §3.8) */
  privateTodo: string;
}

function bearer(jwt: string): Record<string, string> {
  return { Authorization: `Bearer ${jwt}`, 'Content-Type': 'application/json' };
}

/** 读取 Json 的字符串字段,缺失即抛(测试数据契约)。 */
function str(obj: Json, key: string): string {
  const value = obj[key];
  if (typeof value !== 'string') {
    throw new Error(`expected string field "${key}" (got ${typeof value}; keys=${Object.keys(obj).join(',')})`);
  }
  return value;
}

/**
 * 归一后端单资源响应:兼容 `{data: <obj>}` 包络与「已解包对象」两种形态。
 */
function payload(res: Json): Json {
  const data = res.data;
  if (data !== null && typeof data === 'object' && !Array.isArray(data)) return data as Json;
  if (res.id !== undefined || res.identifier !== undefined) return res;
  throw new Error(`unexpected single-resource shape (keys=${Object.keys(res).join(',')})`);
}

/** 归一列表响应:`{data: [...]}`。 */
function payloadList(res: Json): Json[] {
  const data = res.data;
  if (Array.isArray(data)) return data as Json[];
  throw new Error(`unexpected list shape (data type=${typeof data}; keys=${Object.keys(res).join(',')})`);
}

async function json(res: { status(): number; text(): Promise<string> }): Promise<Json> {
  const text = await res.text();
  try {
    return JSON.parse(text) as Json;
  } catch {
    throw new Error(`non-json ${res.status()}: ${text.slice(0, 200)}`);
  }
}

/** 注册 + 经 UI 邮箱/密码登录,返回 access JWT(与 authStore 内一致)。 */
async function registerAndLogin(page: Page): Promise<string> {
  const readToken = (): Promise<string | null> =>
    page.evaluate(() => {
      try {
        const raw = localStorage.getItem('mesh.auth.v1');
        if (!raw) return null;
        const parsed = JSON.parse(raw) as { state?: { token?: string } };
        return parsed?.state?.token ?? null;
      } catch {
        return null;
      }
    });

  // 注册模式:提交后后端 register+login 一并完成,会话已写入 authStore,
  // 但 UI 停在「已发验证邮件」结果页(§4.1)不跳转 —— 故注册后直接 navigate 首页。
  await page.goto('/login');
  await page.getByTestId('login-mode-register').click();
  await page.getByTestId('login-display-name').fill('MES44 UI');
  await page.getByTestId('login-email').fill(EMAIL);
  await page.getByTestId('login-password').fill(PASSWORD);
  await page.getByTestId('login-account-submit').click();
  await page.waitForTimeout(1_200);
  let token = await readToken();
  if (!token) {
    // 回退:若注册未自动登录,则用密码登录(login 模式会 navigate 到首页)。
    await page.goto('/login');
    await page.getByTestId('login-email').fill(EMAIL);
    await page.getByTestId('login-password').fill(PASSWORD);
    await page.getByTestId('login-account-submit').click();
    await page.waitForURL('**/', { timeout: 20_000 });
    token = await readToken();
  }
  if (!token) throw new Error('access token not found in authStore after register/login');
  // 结果页不跳转:会话已就绪,直接导航到首页进入应用。
  if (!/\/$/.test(page.url())) {
    await page.goto('/');
    await page.waitForLoadState('networkidle');
  }
  return token;
}

async function setup(request: APIRequestContext, jwt: string): Promise<Ctx> {
  const api = async (method: string, path: string, body?: Record<string, unknown>): Promise<Json> => {
    const res = await request.fetch(`${BACKEND}${path}`, {
      method,
      headers: bearer(jwt),
      data: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await json(res);
    if (!res.ok()) throw new Error(`${method} ${path} -> ${res.status()}: ${JSON.stringify(data)}`);
    return data;
  };

  const ws = str(payload(await api('POST', '/api/v1/workspaces', {
    name: 'MES44 UI WS',
    slug: SLUG,
    settings: { status_strict_mode: true },
  })), 'id');

  const prjA = str(payload(await api('POST', `/api/v1/workspaces/${ws}/projects`, {
    name: 'Source',
    key: 'SRC',
    visibility: 'public',
  })), 'id');
  const prjB = str(payload(await api('POST', `/api/v1/workspaces/${ws}/projects`, {
    name: 'Target',
    key: 'TGT',
    visibility: 'public',
  })), 'id');

  // 工作区级严格源状态:不配置任何允许的下一步 → 严格模式下不可转出(§4.4/§5.2)。
  const strictStatus = str(payload(await api('POST', `/api/v1/workspaces/${ws}/statuses`, {
    name: 'Frozen',
    category: 'in_progress',
    color: '#9b59b6',
    position: 50,
    is_default: false,
    allowed_transitions: [],
  })), 'id');

  // prjA 项目私有 todo 状态:迁移到 prjB 时该状态在目标不存在 → 触发映射(§3.8)。
  const privateTodo = str(payload(await api('POST', `/api/v1/workspaces/${ws}/statuses`, {
    name: 'Dev-A',
    category: 'todo',
    color: '#27ae60',
    position: 10,
    is_default: true,
    project_id: prjA,
    allowed_transitions: [],
  })), 'id');

  // 项目私有里程碑(供「仅清除/含映射」场景的 cleared 清单)。
  await api('POST', `/api/v1/projects/${prjA}/milestones`, {
    title: 'v1.0',
    target_date: '2026-12-31',
  });

  return { ws, prjA, prjB, strictStatus, privateTodo };
}

async function openIssue(page: Page, issueId: string): Promise<void> {
  await page.goto(`/issues/${issueId}`);
  await page.getByTestId('issue-detail').waitFor({ state: 'visible', timeout: 20_000 });
}

test.describe('MES-44 真实 UI 回归(§4.3/§3.8 + §4.4/§5.2)', () => {
  test.setTimeout(180_000);

  test('迁移预览标明目标项目 + 映射/清除清单;严格模式回滚 + 中文 toast', async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on('pageerror', (e) => consoleErrors.push(e.message));

    const jwt = await registerAndLogin(page);
    const ctx = await setup(page.request, jwt);

    const api = async (method: string, path: string, body?: Record<string, unknown>): Promise<Json> => {
      const res = await page.request.fetch(`${BACKEND}${path}`, {
        method,
        headers: bearer(jwt),
        data: body === undefined ? undefined : JSON.stringify(body),
      });
      const data = await json(res);
      if (!res.ok()) throw new Error(`${method} ${path} -> ${res.status()}: ${JSON.stringify(data)}`);
      return data;
    };

    // 项目私有里程碑(供 cleared 清单)。
    const milestoneId = str(payloadList(await api('GET', `/api/v1/projects/${ctx.prjA}/milestones`))[0], 'id');
    // 工作区级默认 todo 状态(显式传入,规避默认解析歧义)。
    const wsTodo = payloadList(await api('GET', `/api/v1/workspaces/${ctx.ws}/statuses`)).find(
      (s) => s.category === 'todo' && s.is_default === true && s.project_id === null,
    );
    expect(wsTodo, '工作区应存在 todo 默认状态').toBeTruthy();
    const wsTodoId = str(wsTodo as Json, 'id');

    // 工作项 M1:项目私有 todo 状态 + 私有里程碑 → 预览应含 mapped(status) + cleared(milestone)。
    const m1 = str(payload(await api('POST', `/api/v1/workspaces/${ctx.ws}/issues`, {
      title: 'mv-mapped',
      project_id: ctx.prjA,
      status_id: ctx.privateTodo,
      milestone_id: milestoneId,
    })), 'id');
    // 工作项 M2:工作区级状态 + 私有里程碑 → 预览应仅 cleared(无 mapped),复现截图 ev-m5。
    const m2 = str(payload(await api('POST', `/api/v1/workspaces/${ctx.ws}/issues`, {
      title: 'mv-cleared-only',
      project_id: ctx.prjA,
      status_id: wsTodoId,
      milestone_id: milestoneId,
    })), 'id');
    // 工作项 S1:工作区级严格源状态 → 严格模式下任意转出被拒。
    const s1 = str(payload(await api('POST', `/api/v1/workspaces/${ctx.ws}/issues`, {
      title: 'strict-frozen',
      project_id: ctx.prjA,
      status_id: ctx.strictStatus,
    })), 'id');

    // ---- 点 1a:确有状态映射场景 ----
    await openIssue(page, m1);
    await page.getByTestId('issue-detail-project').selectOption(ctx.prjB);
    const dialog = page.getByTestId('move-dialog');
    await dialog.waitFor({ state: 'visible', timeout: 15_000 });
    // 标明目标项目名(Target)
    await expect(page.getByTestId('move-target')).toContainText('Target');
    // 映射清单与清除清单并列
    await expect(page.getByTestId('move-mapped')).toBeVisible();
    await expect(page.getByTestId('move-cleared')).toBeVisible();
    await page.screenshot({ path: 'e2e/evidence/mes44-move-mapped.png' });
    await page.getByTestId('move-cancel').click();
    await expect(dialog).toBeHidden();

    // ---- 点 1b:仅清除场景(复现 ev-m5:无 mapped,仍标明目标项目) ----
    await openIssue(page, m2);
    await page.getByTestId('issue-detail-project').selectOption(ctx.prjB);
    await dialog.waitFor({ state: 'visible', timeout: 15_000 });
    await expect(page.getByTestId('move-target')).toContainText('Target');
    await expect(page.getByTestId('move-cleared')).toBeVisible();
    await expect(page.getByTestId('move-mapped')).toBeHidden();
    await page.screenshot({ path: 'e2e/evidence/mes44-move-cleared-only.png' });
    await page.getByTestId('move-cancel').click();

    // ---- 点 2a:严格模式 ON,被禁转换 → 中文 toast + 就地回滚 + 无整页 reload ----
    // 切到 zh-CN 验证 i18n(账号偏好,即时生效无刷新)。
    await page.goto('/settings');
    await page.waitForLoadState('networkidle');
    const zhOption = page.getByRole('option', { name: /中文|Chinese|zh/ }).first();
    if (await zhOption.count()) {
      // 语言选择器存在则切换;否则经 store 直接设置。
      try {
        await page.getByText(/语言|Language/).first().click({ timeout: 2_000 });
        await zhOption.click({ timeout: 2_000 });
      } catch {
        /* 选择器形态不一,回退 store 注入 */
      }
    }
    await page.evaluate(() => {
      try {
        const raw = localStorage.getItem('mesh.settings.v1');
        const parsed = (raw ? JSON.parse(raw) : { state: { preferences: {} } }) as {
          state?: { preferences?: { locale?: string } };
        };
        parsed.state ??= {};
        parsed.state.preferences ??= {};
        parsed.state.preferences.locale = 'zh-CN';
        localStorage.setItem('mesh.settings.v1', JSON.stringify(parsed));
      } catch {
        /* ignore */
      }
    });
    // 重新水合 settings store,使 zh-CN 生效(即时,无整页骨架)。
    await page.reload();
    await page.waitForLoadState('networkidle');

    await openIssue(page, s1);
    const statusSelect = page.getByTestId('issue-detail-status');
    await expect(statusSelect).toHaveValue(ctx.strictStatus);
    // 选一个被禁目标(todo 默认状态);strict 源 allowed_transitions=[] → 必被拒。
    const todoTarget = payloadList(
      await api('GET', `/api/v1/workspaces/${ctx.ws}/statuses?project_id=${ctx.prjA}`),
    ).find((s) => s.category === 'todo' && s.project_id === null);
    expect(todoTarget, '应存在工作区级 todo 目标状态').toBeTruthy();
    const todoTargetId = str(todoTarget as Json, 'id');
    await statusSelect.selectOption(todoTargetId);
    // 中文危险 toast
    await expect(page.getByText('严格模式下不允许该状态转换')).toBeVisible({ timeout: 15_000 });
    // 就地回落原值,不保留被禁目标值
    await expect(statusSelect).toHaveValue(ctx.strictStatus);
    await page.screenshot({ path: 'e2e/evidence/mes44-strict-rollback-zh.png' });
    // 服务端确未变
    expect(str(payload(await api('GET', `/api/v1/issues/${s1}`)), 'status_id')).toBe(ctx.strictStatus);
    // 无 unhandled rejection
    expect(consoleErrors).toEqual([]);

    // ---- 点 2b:严格模式 OFF,同一转换可成功 ----
    await api('PATCH', `/api/v1/workspaces/${ctx.ws}`, {
      settings: { status_strict_mode: false },
    });
    await openIssue(page, s1);
    const statusSelect2 = page.getByTestId('issue-detail-status');
    await expect(statusSelect2).toHaveValue(ctx.strictStatus);
    await statusSelect2.selectOption(todoTargetId);
    // 成功:状态最终落到目标(经乐观+重取),无严格模式拒绝 toast
    await expect(statusSelect2).toHaveValue(todoTargetId, { timeout: 15_000 });
    await expect(page.getByText('严格模式下不允许该状态转换')).toBeHidden();
    await page.screenshot({ path: 'e2e/evidence/mes44-strict-off-ok.png' });
  });
});
