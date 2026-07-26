/**
 * 真实后端 label-property 定义层浏览器走查(MES-42 §4 验收):注册/登录 → 建区 →
 * 工作区设置 → 标签管理(新建 / 重名 409 / 编辑 / 删除)→ 自定义字段(枚举字段 +
 * 初始选项 / 非法配置 422 / 停用 / 选项编辑器)→ 项目设置内项目级标签与字段。
 * 每步截图存证,默认写入 e2e/evidence/labels(随 PR 提交,可复现)。
 *
 * 前置:真实后端栈运行中(MESH_AUTH_MODE=dev,8000/8081);dev server 由
 * playwright.real.config.ts 拉起并指向真实后端。
 */
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const RUN = String(Date.now()).slice(-7);
const EMAIL = `labels-${RUN}@corp.example`;
const PASSWORD = 'secret123';
const SLUG = `lbl${RUN}`;
const EVIDENCE_DIR = process.env.MES42_EVIDENCE_DIR ?? resolve(HERE, 'evidence', 'labels');

test.describe.configure({ mode: 'serial' });

async function registerAndLogin(page: Page): Promise<void> {
  await page.goto('/login');
  await page.getByTestId('login-mode-register').click();
  await page.getByTestId('login-display-name').fill('Labels Owner');
  await page.getByTestId('login-email').fill(EMAIL);
  await page.getByTestId('login-password').fill(PASSWORD);
  await page.getByTestId('login-account-submit').click();
  // 注册后进入「查收验证邮件」过渡态(dev 模式令牌存 Redis dev-mailbox);
  // 会话已建立,点「继续」进入主壳。
  await page.getByTestId('register-continue').click();
  await expect(page.locator('.mesh-shell')).toBeVisible({ timeout: 30_000 });
}

async function createWorkspace(page: Page): Promise<void> {
  await page.getByTestId('ws-switcher-button').click();
  await page.getByTestId('ws-switcher-create').click();
  await page.getByTestId('ws-wizard-name-input').fill('Labels Walkthrough');
  await page.getByTestId('ws-wizard-next').click();
  await page.getByTestId('ws-wizard-slug-input').fill(SLUG);
  await page.waitForTimeout(800);
  await page.getByTestId('ws-wizard-next-slug').click();
  await page.waitForTimeout(500);
  await page.getByTestId('ws-wizard-skip').click();
  await expect(page).toHaveURL(new RegExp(`/w/${SLUG}`), { timeout: 15_000 });
}

test('label-property 定义层真实走查 + 截图存证', async ({ page }) => {
  await registerAndLogin(page);
  await createWorkspace(page);

  // ---- 标签管理页(工作区设置 → 标签)--------------------------------------
  await page.goto(`/w/${SLUG}/settings/labels`);
  await expect(page.getByTestId('labels-panel')).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/No labels yet|还没有标签/)).toBeVisible();
  await page.screenshot({ path: `${EVIDENCE_DIR}/01-labels-empty.png` });

  // 新建标签:名称 + 预设色板 + 描述
  await page.getByTestId('labels-create').click();
  await page.getByTestId('label-name-input').fill('bug');
  await page.getByRole('radio', { name: '#e5484d' }).check();
  await page.getByTestId('label-description-input').fill('缺陷');
  await page.getByTestId('label-save').click();
  await expect(page.getByTestId('label-row-bug')).toBeVisible({ timeout: 10_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/02-label-created.png` });

  // 重名 → 409 label_name_taken 内联呈现
  await page.getByTestId('labels-create').click();
  await page.getByTestId('label-name-input').fill('bug');
  await page.getByTestId('label-save').click();
  await expect(page.getByTestId('label-form-error')).toBeVisible({ timeout: 10_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/03-label-name-taken.png` });
  await page.getByRole('button', { name: /Cancel|取消/ }).click();
  await expect(page.getByTestId('label-name-input')).toBeHidden();

  // 编辑标签(If-Match 乐观并发;改名 + 换色)
  await page.getByTestId('label-edit-bug').click();
  const editName = page.getByTestId('label-name-input');
  await editName.fill('defect');
  await page.getByRole('radio', { name: '#f5a623' }).check();
  await page.getByTestId('label-save').click();
  await expect(page.getByTestId('label-row-defect')).toBeVisible({ timeout: 10_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/04-label-edited.png` });

  // ---- 自定义字段页 ---------------------------------------------------------
  await page.goto(`/w/${SLUG}/settings/custom-fields`);
  await expect(page.getByTestId('custom-fields-panel')).toBeVisible({ timeout: 15_000 });

  // 非法配置:text 类型携带 number 的 config 不会在 UI 发生;走 field_key 格式校验路径
  await page.getByTestId('fields-create').click();
  await page.getByTestId('field-name-input').fill('Bad Key');
  await page.getByTestId('field-key-input').fill('Not-Valid');
  await page.getByTestId('field-save').click();
  await expect(page.getByTestId('field-form-error')).toBeVisible({ timeout: 10_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/05-field-key-invalid.png` });
  // 修正 field_key 后继续提交(同一对话框内走完正常路径)
  await page.getByTestId('field-key-input').fill('severity');

  // 切换单选类型 → 选项编辑器出现;填两个选项
  await page.getByTestId('field-type-select').selectOption('single_select');
  await expect(page.getByTestId('field-options-editor')).toBeVisible();
  await page.getByTestId('field-option-name-0').fill('Major');
  await page.getByTestId('field-option-add').click();
  await page.getByTestId('field-option-name-1').fill('Critical');
  await page.getByTestId('field-required-checkbox').check();
  await page.getByTestId('field-save').click();
  await expect(page.getByTestId('field-row-severity')).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(/required|必填/).first()).toBeVisible();
  await page.screenshot({ path: `${EVIDENCE_DIR}/06-field-created.png` });

  // 选项编辑器:新增选项 + 停用选项
  await page.getByTestId('field-options-severity').click();
  await expect(page.getByTestId('options-editor')).toBeVisible();
  await expect(page.getByTestId('option-row-Major')).toBeVisible();
  await page.getByTestId('option-new-name').fill('Minor');
  await page.getByTestId('option-add-confirm').click();
  // 添加成功后对话框关闭并刷新;重新打开验证 Minor 落库
  await expect(page.getByTestId('field-row-severity')).toBeVisible({ timeout: 10_000 });
  await page.getByTestId('field-options-severity').click();
  await expect(page.getByTestId('option-row-Minor')).toBeVisible({ timeout: 10_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/07-options-editor.png` });
  await page.getByTestId('option-toggle-Minor').click();
  await page.getByTestId('field-options-severity').click();
  await expect(page.getByText(/inactive|停用/).first()).toBeVisible({ timeout: 10_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/08-option-inactive.png` });
  await page.locator('.mesh-dialog__close').click();
  await expect(page.getByTestId('options-editor')).toBeHidden();

  // 停用字段 → inactive 徽章
  await page.getByTestId('field-toggle-severity').click();
  await expect(page.getByText(/inactive|停用/).first()).toBeVisible({ timeout: 10_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/09-field-inactive.png` });

  // ---- 项目设置内的项目级标签 / 字段(§4.1 项目设置)------------------------
  // 建一个项目
  await page.goto('/projects');
  await page.getByTestId('new-project-button').click();
  await page.getByLabel('Name').fill('Site Revamp');
  await page.getByLabel(/Key/).fill(`LX${RUN.slice(0, 3).toUpperCase()}`);
  await page.getByTestId('create-project-submit').click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]+$/, { timeout: 15_000 });
  const projectId = (page.url().match(/\/projects\/([0-9a-f-]+)$/) ?? [])[1];
  expect(projectId).toBeTruthy();

  await page.goto(`/projects/${projectId}/settings`);
  await expect(page.getByTestId('labels-panel')).toBeVisible({ timeout: 15_000 });
  // 项目级新建标签(工作区级 defect 也出现在列表中)
  await page.getByTestId('labels-create').click();
  await page.getByTestId('label-name-input').fill('customer-a');
  await page.getByRole('radio', { name: '#3e63dd' }).check();
  await page.getByTestId('label-save').click();
  await expect(page.getByTestId('label-row-customer-a')).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId('label-row-defect')).toBeVisible();
  await page.screenshot({ path: `${EVIDENCE_DIR}/10-project-labels.png` });

  // 项目级自定义字段
  await page.getByTestId('fields-create').click();
  await page.getByTestId('field-name-input').fill('Impact');
  await page.getByTestId('field-key-input').fill('impact');
  await page.getByTestId('field-type-select').selectOption('number');
  await page.getByTestId('field-save').click();
  await expect(page.getByTestId('field-row-impact')).toBeVisible({ timeout: 10_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/11-project-fields.png` });

  // ---- 删除标签(二次确认)--------------------------------------------------
  await page.goto(`/w/${SLUG}/settings/labels`);
  await expect(page.getByTestId('label-row-defect')).toBeVisible({ timeout: 15_000 });
  await page.getByTestId('label-delete-defect').click();
  await page.getByTestId('label-delete-confirm').click();
  await expect(page.getByTestId('label-row-defect')).toBeHidden({ timeout: 10_000 });
  await page.screenshot({ path: `${EVIDENCE_DIR}/12-label-deleted.png` });
});
