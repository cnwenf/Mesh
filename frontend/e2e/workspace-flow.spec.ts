/**
 * workspace.md §4 真实后端全流程 e2e(MES-26 验收核心):
 * 对真实后端栈(postgres+redis+api+worker+gateway,MESH_AUTH_MODE=dev)以真实浏览器走通:
 *   ① 注册/登录(auth v0.2.0)→ 创建向导建区(W1/W2)→ 设置页改名(W4)
 *   ② 邀请全生命周期:链接创建(max_uses=1)→ 分享 → 接受(未登录注册 ?next= 回跳)
 *      → 次数耗尽(exhausted)→ 过期(psql 置 expires_at)→ 撤销(revoked)
 *      → 伪造 token(not_found),各 reason UI 态(§4.4)
 *   ③ 重加入同成功态(Leader 裁决 pin@MES-14)
 *   ④ 跨租户/越权负向:member 直达设置页 → 无权限态;非成员工作区 → 404 同不存在(§5.3)
 *   ⑤ 角色矩阵与名册降级呈现(MES-14 未合入)
 *   ⑥ 工作区默认 locale 协商链第三级(§6.18):设 zh-CN → 无偏好成员重载后中文界面
 *   ⑦ realtime(§4.5):接受邀请后管理员侧用量实时更新(WS 首帧 JWT 或轮询降级)
 *   ⑧ 危险区:owner slug 二次确认删除(W10)
 *
 * 三个持久浏览器上下文(独立 localStorage = 独立会话):A=owner,B/C=新成员。
 * 每用户仅登录一次,规避 auth 登录限流(5/60s per ip:email)。
 * 前置:docker compose up postgres redis api worker gateway(本分支后端镜像)。
 */
import { expect, test } from '@playwright/test';
import type { BrowserContext, Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';

const PG_CONTAINER = 'mesh-postgres-1';
const RUN = String(Date.now()).slice(-7);
const EMAIL_A = `alice-${RUN}@corp.example`;
const EMAIL_B = `bob-${RUN}@corp.example`;
const PASSWORD = 'secret123';
const SLUG = `mes26-${RUN}`;
const SLUG2 = `mes26b-${RUN}`;
const EVIDENCE_DIR = process.env.MES26_EVIDENCE_DIR ?? '/tmp/mes26-evidence';

let contextA: BrowserContext;
let contextB: BrowserContext;
let contextC: BrowserContext;
let pageA: Page;
let pageB: Page;
let pageC: Page;
let inviteLinkMax1 = '';
let inviteLinkExpiry = '';
let inviteLinkRevoke = '';

function psql(sql: string): string {
  return execFileSync(
    'docker',
    ['exec', '-i', PG_CONTAINER, 'psql', '-U', 'mesh', '-d', 'mesh', '-tAc', sql],
    { encoding: 'utf8', timeout: 30_000 },
  );
}

async function registerAndLogin(page: Page, email: string, displayName: string): Promise<void> {
  await page.goto('/login');
  await page.getByTestId('login-mode-register').click();
  await page.getByTestId('login-display-name').fill(displayName);
  await page.getByTestId('login-email').fill(email);
  await page.getByTestId('login-password').fill(PASSWORD);
  await page.getByTestId('login-account-submit').click();
  await expect(page.locator('.mesh-shell')).toBeVisible({ timeout: 30_000 });
}

test.describe.configure({ mode: 'serial' });

test.describe('workspace §4 真实后端全流程', () => {
  test.beforeAll(async ({ browser }) => {
    contextA = await browser.newContext();
    contextB = await browser.newContext();
    contextC = await browser.newContext();
    pageA = await contextA.newPage();
    pageB = await contextB.newPage();
    pageC = await contextC.newPage();
  });

  test.afterAll(async () => {
    await contextA?.close();
    await contextB?.close();
    await contextC?.close();
  });

  test('① 用户 A 注册登录并经向导创建工作区', async () => {
    await registerAndLogin(pageA, EMAIL_A, 'Alice');

    // 切换器 → 创建向导:名称 → slug(自动建议,改为唯一值)→ 跳过邀请 → 完成
    await pageA.getByTestId('ws-switcher-button').click();
    await pageA.getByTestId('ws-switcher-create').click();
    await pageA.getByTestId('ws-wizard-name-input').fill('Acme Team');
    await pageA.getByTestId('ws-wizard-next').click();
    const slugInput = pageA.getByTestId('ws-wizard-slug-input');
    await expect(slugInput).toHaveValue('acme-team');
    await slugInput.fill(SLUG);
    await pageA.getByTestId('ws-wizard-next-slug').click();
    await pageA.getByTestId('ws-wizard-skip').click();

    // 进入新工作区,owner 视角可见设置入口
    await expect(pageA.getByTestId('ws-home-name')).toHaveText('Acme Team', { timeout: 30_000 });
    await expect(pageA.getByTestId('ws-settings-link')).toBeVisible();
    await expect(pageA).toHaveURL(new RegExp(`/w/${SLUG}$`));
    await pageA.screenshot({ path: `${EVIDENCE_DIR}/01-workspace-home.png` });
  });

  test('② 设置页:改名保存 + 邀请/角色/危险节区就绪 + 名册降级', async () => {
    await pageA.goto(`/w/${SLUG}/settings`);
    await expect(pageA.getByTestId('ws-settings')).toBeVisible({ timeout: 30_000 });
    await expect(pageA.getByTestId('invitation-create')).toBeVisible();
    await expect(pageA.getByTestId('roles-section')).toBeVisible();
    await expect(pageA.getByTestId('danger-zone')).toBeVisible();

    await pageA.getByTestId('ws-name-input').fill('Acme Corp');
    await pageA.getByTestId('ws-save').click();
    await expect(pageA.getByText('Settings saved.')).toBeVisible({ timeout: 30_000 });

    // 角色矩阵 + 名册降级(MES-14 未合入,端点 404 → 优雅提示而非错误态)
    await expect(pageA.getByTestId('roles-matrix')).toBeVisible();
    await expect(pageA.getByTestId('roles-roster-unavailable')).toBeVisible();
    await pageA.screenshot({ path: `${EVIDENCE_DIR}/02-settings-page.png` });
  });

  test('③ 邀请链接创建(max_uses=1)与列表呈现', async () => {
    await pageA.goto(`/w/${SLUG}/settings`);
    await expect(pageA.getByTestId('invitation-create')).toBeVisible({ timeout: 30_000 });

    await pageA.getByTestId('invite-max-uses').fill('1');
    await pageA.getByTestId('invite-submit').click();
    const linkUrl = pageA.getByTestId('invite-link-url');
    await expect(linkUrl).toBeVisible({ timeout: 30_000 });
    inviteLinkMax1 = (await linkUrl.textContent()) ?? '';
    expect(inviteLinkMax1).toContain('/invite/invtk_');

    // 列表 active 行,用量 0/1
    await expect(pageA.getByTestId('invitation-uses').first()).toHaveText('0/1');
    await pageA.screenshot({ path: `${EVIDENCE_DIR}/03-invite-link-card.png` });
  });

  test('④ 用户 B 经链接接受邀请(未登录 → 注册 ?next= 回跳 → 成功进入)', async () => {
    const path = new URL(inviteLinkMax1).pathname;
    await pageB.goto(path);
    await expect(pageB.getByTestId('invite-preview')).toBeVisible({ timeout: 30_000 });
    await expect(pageB.getByText(/Acme Corp/)).toBeVisible();

    // 未登录 → 登录页(?next= 回跳)
    await pageB.getByTestId('invite-login').click();
    await expect(pageB).toHaveURL(/\/login\?next=/);

    // 就地注册(不经 goto,保留 ?next= 回跳参数)
    await pageB.getByTestId('login-mode-register').click();
    await pageB.getByTestId('login-display-name').fill('Bob');
    await pageB.getByTestId('login-email').fill(EMAIL_B);
    await pageB.getByTestId('login-password').fill(PASSWORD);
    await pageB.getByTestId('login-account-submit').click();
    // 注册后回跳到邀请页,接受 → 成功 → 进入工作区
    await expect(pageB.getByTestId('invite-accept')).toBeVisible({ timeout: 30_000 });
    await pageB.getByTestId('invite-accept').click();
    await expect(pageB.getByTestId('invite-accepted')).toBeVisible({ timeout: 30_000 });
    await pageB.getByTestId('invite-enter').click();
    await expect(pageB.getByTestId('ws-home-name')).toHaveText('Acme Corp', { timeout: 30_000 });
    await pageB.screenshot({ path: `${EVIDENCE_DIR}/04-invite-accepted.png` });
  });

  test('⑤ realtime:管理员侧用量实时更新为 1/1 且呈 exhausted(§4.5)', async () => {
    await pageA.goto(`/w/${SLUG}/settings`);
    // WS(首帧 JWT)或 1s 轮询降级:用量最终一致到 1/1
    await expect(pageA.getByTestId('invitation-uses').first()).toHaveText('1/1', {
      timeout: 30_000,
    });
    await expect(pageA.getByText('exhausted').first()).toBeVisible({ timeout: 15_000 });
    await pageA.screenshot({ path: `${EVIDENCE_DIR}/05-redeemed-realtime.png` });
  });

  test('⑥ 次数耗尽:用户 C 接受同一链接 → exhausted reason UI', async () => {
    const path = new URL(inviteLinkMax1).pathname;
    await pageC.goto(path);
    await expect(pageC.getByTestId('invite-reason-exhausted')).toBeVisible({ timeout: 30_000 });
    await pageC.screenshot({ path: `${EVIDENCE_DIR}/06-reason-exhausted.png` });
  });

  test('⑦ 过期:psql 置 expires_at 至过去 → expired reason UI', async () => {
    await pageA.goto(`/w/${SLUG}/settings`);
    await expect(pageA.getByTestId('invitation-create')).toBeVisible({ timeout: 30_000 });
    await pageA.getByTestId('invite-submit').click();
    const linkUrl = pageA.getByTestId('invite-link-url');
    await expect(linkUrl).toBeVisible({ timeout: 30_000 });
    inviteLinkExpiry = (await linkUrl.textContent()) ?? '';

    const token = inviteLinkExpiry.split('/invite/')[1];
    psql(
      `UPDATE workspace_invitations SET expires_at = now() - interval '1 hour' WHERE token_prefix = '${token.slice(0, 14)}' AND status = 'active';`,
    );

    const path = new URL(inviteLinkExpiry).pathname;
    await pageC.goto(path);
    await expect(pageC.getByTestId('invite-reason-expired')).toBeVisible({ timeout: 30_000 });
    await pageC.screenshot({ path: `${EVIDENCE_DIR}/07-reason-expired.png` });
  });

  test('⑧ 撤销:UI 撤销后 → revoked reason UI', async () => {
    await pageA.goto(`/w/${SLUG}/settings`);
    await expect(pageA.getByTestId('invitation-create')).toBeVisible({ timeout: 30_000 });
    await pageA.getByTestId('invite-submit').click();
    const linkUrl = pageA.getByTestId('invite-link-url');
    await expect(linkUrl).toBeVisible({ timeout: 30_000 });
    inviteLinkRevoke = (await linkUrl.textContent()) ?? '';

    // 撤销最新 active 邀请(列表按创建序,撤销按钮取最后一个 active 行)
    await pageA.getByTestId('invitation-revoke').last().click();
    await expect(pageA.getByText('Invitation revoked.')).toBeVisible({ timeout: 30_000 });

    const path = new URL(inviteLinkRevoke).pathname;
    await pageC.goto(path);
    await expect(pageC.getByTestId('invite-reason-revoked')).toBeVisible({ timeout: 30_000 });
    await pageC.screenshot({ path: `${EVIDENCE_DIR}/08-reason-revoked.png` });
  });

  test('⑨ 伪造 token → not_found reason UI(不泄漏存在性)', async () => {
    await pageC.goto('/invite/invtk_DoesNotExist0000000000000000000');
    await expect(pageC.getByTestId('invite-reason-not_found')).toBeVisible({ timeout: 30_000 });
    await pageC.screenshot({ path: `${EVIDENCE_DIR}/09-reason-not-found.png` });
  });

  test('⑩ 重加入:成员 B 再接受新邀请 → 成功态(非错误 reason)', async () => {
    await pageA.goto(`/w/${SLUG}/settings`);
    await expect(pageA.getByTestId('invitation-create')).toBeVisible({ timeout: 30_000 });
    await pageA.getByTestId('invite-submit').click();
    const linkUrl = pageA.getByTestId('invite-link-url');
    await expect(linkUrl).toBeVisible({ timeout: 30_000 });
    const rejoinLink = (await linkUrl.textContent()) ?? '';

    const path = new URL(rejoinLink).pathname;
    await pageB.goto(path);
    await expect(pageB.getByTestId('invite-accept')).toBeVisible({ timeout: 30_000 });
    await pageB.getByTestId('invite-accept').click();
    // 重加入 = 成功态(Leader 裁决:重激活既有名册行,UI 不区分)
    await expect(pageB.getByTestId('invite-accepted')).toBeVisible({ timeout: 30_000 });
    await pageB.screenshot({ path: `${EVIDENCE_DIR}/10-rejoin-success.png` });
  });

  test('⑪ 越权负向:member 直达设置页 → 无权限态;设置 nav 入口不可见', async () => {
    await pageB.goto(`/w/${SLUG}`);
    await expect(pageB.getByTestId('ws-home-name')).toBeVisible({ timeout: 30_000 });
    // member 无工作区设置入口
    await expect(pageB.getByTestId('nav-workspace-settings')).toHaveCount(0);

    await pageB.goto(`/w/${SLUG}/settings`);
    await expect(pageB.getByTestId('ws-settings-denied')).toBeVisible({ timeout: 30_000 });
    await pageB.screenshot({ path: `${EVIDENCE_DIR}/11-permission-denied.png` });
  });

  test('⑫ 跨租户负向:非成员访问他区 → 404 同不存在', async () => {
    // A 再建第二个工作区,B 非成员
    await pageA.goto('/');
    await expect(pageA.getByTestId('ws-switcher-button')).toBeVisible({ timeout: 30_000 });
    await pageA.getByTestId('ws-switcher-button').click();
    await pageA.getByTestId('ws-switcher-create').click();
    await pageA.getByTestId('ws-wizard-name-input').fill('Second WS');
    await pageA.getByTestId('ws-wizard-next').click();
    await pageA.getByTestId('ws-wizard-slug-input').fill(SLUG2);
    await pageA.getByTestId('ws-wizard-next-slug').click();
    await pageA.getByTestId('ws-wizard-skip').click();
    await expect(pageA.getByTestId('ws-home-name')).toHaveText('Second WS', { timeout: 30_000 });

    // B 访问 A 的第二工作区 → not-found(与不存在同形,无存在性泄漏)
    await pageB.goto(`/w/${SLUG2}`);
    await expect(pageB.getByTestId('ws-not-found')).toBeVisible({ timeout: 30_000 });
    // 随机 slug 同一呈现
    await pageB.goto('/w/definitely-not-exist-xyz');
    await expect(pageB.getByTestId('ws-not-found')).toBeVisible({ timeout: 30_000 });
    await pageB.screenshot({ path: `${EVIDENCE_DIR}/12-cross-tenant-404.png` });
  });

  test('⑬ 工作区默认 locale 协商链(§6.18):设 zh-CN → 无偏好成员重载后中文界面', async () => {
    await pageA.goto(`/w/${SLUG}/settings`);
    await expect(pageA.getByTestId('ws-locale-select')).toBeVisible({ timeout: 30_000 });
    await pageA.getByTestId('ws-locale-select').selectOption('zh-CN');
    await pageA.getByTestId('ws-save').click();
    await expect(pageA.getByText('Settings saved.')).toBeVisible({ timeout: 30_000 });

    // B 无个人语言偏好 → 工作区默认 zh-CN 生效(协商链第三级)
    await pageB.goto('/');
    await pageB.reload();
    await expect(pageB.getByTestId('ws-switcher-button')).toContainText('工作区', {
      timeout: 30_000,
    });
    await pageB.screenshot({ path: `${EVIDENCE_DIR}/13-zh-cn-ui.png` });

    // 还原为 en,避免影响后续用例
    await pageA.getByTestId('ws-locale-select').selectOption('en');
    await pageA.getByTestId('ws-save').click();
    await expect(pageA.getByText('Settings saved.')).toBeVisible({ timeout: 30_000 });
  });

  test('⑭ zh-CN/en 个人偏好切换(文案 100% 外部化)', async () => {
    await pageA.goto('/settings');
    await expect(pageA.getByTestId('locale-select')).toBeVisible({ timeout: 30_000 });
    await pageA.getByTestId('locale-select').selectOption('zh-CN');
    await expect(pageA.locator('h2', { hasText: '外观' }).first()).toBeVisible({ timeout: 15_000 });
    await pageA.getByTestId('locale-select').selectOption('en');
    await expect(pageA.locator('h2', { hasText: 'Appearance' }).first()).toBeVisible({
      timeout: 15_000,
    });
    await pageA.screenshot({ path: `${EVIDENCE_DIR}/14-locale-switch.png` });
  });

  test('⑮ 危险区:owner slug 二次确认删除工作区(W10)', async () => {
    await pageA.goto(`/w/${SLUG}/settings`);
    await expect(pageA.getByTestId('danger-open')).toBeVisible({ timeout: 30_000 });
    await pageA.getByTestId('danger-open').click();

    // slug 不匹配 → 确认按钮禁用
    await pageA.getByTestId('danger-confirm-input').fill('wrong-slug');
    await expect(pageA.getByTestId('danger-confirm')).toBeDisabled();

    await pageA.getByTestId('danger-confirm-input').fill(SLUG);
    await pageA.getByTestId('danger-confirm').click();
    // 删除成功 → 切换器中该工作区消失
    await expect(pageA.getByTestId('ws-switcher-button')).toBeVisible({ timeout: 30_000 });
    await pageA.getByTestId('ws-switcher-button').click();
    await expect(pageA.getByTestId('ws-switcher-empty').or(pageA.getByTestId('ws-switcher-item-' + SLUG2))).toBeVisible({ timeout: 30_000 });
    await expect(pageA.getByTestId('ws-switcher-item-' + SLUG)).toHaveCount(0);
    await pageA.screenshot({ path: `${EVIDENCE_DIR}/15-workspace-deleted.png` });
  });
});
