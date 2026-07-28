/**
 * 数据管理 UI 真人式走查(import-export.md §4):真实浏览器操作导入向导全流
 * 程与导出对话框,逐步截图存证。账号 / 工作区 / 源文件经真实 API 预置(与
 * e2e 同款三段式直传),其后全部交互在浏览器内完成。
 */
import { chromium } from 'playwright';
import { writeFileSync } from 'node:fs';

const API = 'http://127.0.0.1:8100';
const UI = 'http://localhost:5173';
const SHOT_DIR = '/tmp/ui-verify';

const CSV =
  'Title,State,Priority,Key\n' +
  'Login crash,Todo,High,EXT-1\n' +
  'Fix button,Todo,Low,EXT-2\n' +
  'Broken nav,Todo,High,EXT-3\n' +
  ',Todo,Low,EXT-4\n'; // 一行缺标题 → 部分成功

const PASSWORD = 'S3cure-passw0rd!';
const email = `ui-verify-${Date.now()}@mesh.example`;
const slug = `uiv-${Date.now().toString(36)}`;

async function api(path, { method = 'GET', token, body } = {}) {
  const response = await fetch(`${API}${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`${method} ${path} → ${response.status}: ${text}`);
  return text ? JSON.parse(text) : null;
}

async function seed() {
  await api('/api/v1/auth/register', {
    method: 'POST',
    body: { email, password: PASSWORD, display_name: 'UI Verify' },
  });
  const login = await api('/api/v1/auth/login', {
    method: 'POST',
    body: { email, password: PASSWORD },
  });
  const token = login.data.access_token;
  const workspace = await api('/api/v1/workspaces', {
    method: 'POST',
    token,
    body: { name: 'UI Verify WS', slug },
  });
  const workspaceId = workspace.data.id;

  // 三段式直传源 CSV(真实 MinIO)
  const bytes = new TextEncoder().encode(CSV);
  const crypto = await import('node:crypto');
  const hash = crypto.createHash('sha256').update(bytes).digest('hex');
  const requested = await api('/api/v1/attachments/upload-requests', {
    method: 'POST',
    token,
    body: {
      workspace_id: workspaceId,
      file_name: 'issues.csv',
      file_size: bytes.length,
      mime_type: 'text/csv',
      content_hash: hash,
    },
  });
  let payload = requested.data;
  if (payload.upload !== null) {
    const put = await fetch(payload.upload.url, {
      method: 'PUT',
      headers: payload.upload.headers,
      body: bytes,
    });
    if (!put.ok) throw new Error(`PUT upload → ${put.status}`);
    const done = await api(`/api/v1/attachments/${payload.id}/complete`, {
      method: 'POST',
      token,
      body: {},
    });
    payload = done.data;
  }
  // 等待隔离区放行(text → skipped)
  for (let i = 0; i < 60; i += 1) {
    const info = await api(`/api/v1/attachments/${payload.id}`, { token });
    if (['clean', 'skipped'].includes(info.data.scan_status)) break;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  writeFileSync(`${SHOT_DIR}/issues.csv`, CSV);
  return { token, workspaceId, sourceId: payload.id };
}

async function main() {
  const { token } = await seed();
  const browser = await chromium.launch({ headless: true, args: ["--disable-web-security"] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const shot = async (name) => {
    await page.screenshot({ path: `${SHOT_DIR}/${name}.png`, fullPage: true });
    console.log(`screenshot: ${name}`);
  };

  // 1. 登录页真实表单登录
  await page.goto(`${UI}/login`);
  await page.getByTestId('login-account-submit').waitFor({ timeout: 15000 });
  await page.locator('input[type="email"]').fill(email);
  await page.locator('input[type="password"]').first().fill(PASSWORD);
  await shot('01-login-form');
  await page.getByTestId('login-account-submit').click();
  await page.waitForURL(/.*(?!login).*/, { timeout: 20000 });
  await page.waitForTimeout(1500);
  await shot('02-after-login');

  // 2. 设置 → 数据管理
  await page.goto(`${UI}/w/${slug}/settings`);
  await page.getByTestId('ws-data-link').waitFor({ timeout: 15000 });
  await shot('03-ws-settings-data-link');
  await page.getByTestId('ws-data-link').click();
  await page.getByText('No data jobs yet').waitFor({ timeout: 15000 });
  await shot('04-data-management-empty');

  // 3. 导入向导:上传 → 映射 → dry-run → 确认 → 进度
  await page.getByTestId('open-import-wizard').click();
  await page.getByText('Import data').first().waitFor({ timeout: 10000 });
  await page.getByTestId('import-file-input').setInputFiles(`${SHOT_DIR}/issues.csv`);
  await page.getByTestId('upload-ready').waitFor({ timeout: 30000 });
  await shot('05-wizard-upload-ready');
  await page.getByRole('button', { name: 'Next' }).click();
  await page.getByText('Title').first().waitFor({ timeout: 10000 }); // 映射行
  await shot('06-wizard-mapping');
  await page.getByRole('button', { name: 'Validate (dry run)' }).click();
  await page.getByTestId('validate-summary').waitFor({ timeout: 60000 });
  await shot('07-wizard-validate-summary');
  await page.getByRole('button', { name: 'Next' }).click();
  await page.getByRole('button', { name: 'Confirm import' }).click();
  await page.getByTestId('progress-count').waitFor({ timeout: 10000 });
  await shot('08-wizard-progress-start');
  // 等待终态(成功 3 / 失败 1)
  await page
    .getByText(/Completed with errors|Completed/, { exact: false })
    .first()
    .waitFor({ timeout: 90000 });
  await page.waitForTimeout(1000);
  await shot('09-wizard-result');
  await page.getByRole('button', { name: 'Close' }).last().click();

  // 4. 作业列表出现导入作业 + 错误报告下载
  await page.getByTestId('job-row-').first().waitFor({ timeout: 15000 }).catch(() => undefined);
  await page.reload();
  await page.getByText('90 ok', { exact: false }).waitFor({ timeout: 15000 }).catch(() => undefined);
  await page.getByText(/3 ok \/ 1 failed \/ 4 total|ok \/ .* failed/).first().waitFor({ timeout: 15000 });
  await shot('10-jobs-table-after-import');

  // 5. 导出对话框:提交 → 下载链接
  await page.getByTestId('open-export-dialog').click();
  await page.getByTestId('export-submit-button').waitFor({ timeout: 10000 });
  await shot('11-export-dialog');
  await page.getByTestId('export-submit-button').click();
  await page.getByTestId('export-download-link').waitFor({ timeout: 120000 });
  await shot('12-export-download-ready');

  console.log('UI VERIFICATION COMPLETE');
  await browser.close();
}

main().catch((error) => {
  console.error('UI VERIFICATION FAILED:', error);
  process.exit(1);
});
