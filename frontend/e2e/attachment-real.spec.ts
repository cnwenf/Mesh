/**
 * 真实后端附件模块浏览器走查(MES-59 §4/§5 验收,零 mock):
 * 注册/登录(UI)→ 建区/建 issue(API 脚手架)→ issue 详情附件区经 composer
 * 真实上传(PUT 直传 MinIO,字节流不经应用服务器)→ 扫描中占位(可见性闸门
 * T14 UI 态)→ worker 放行后缩略图经 attachment.processed 实时出现 → 灯箱
 * (缩放/旋转/重置/定位/下载,§4.3)→ 逐字节下载核验 → 秒传(T24:同 hash 可
 * 读即免传;他人 hash 探测不得短路)→ 文本文件卡片 → 删除。每步截图存证。
 *
 * 前置:真实后端栈运行中(MESH_AUTH_MODE=dev,api :8000 / gateway :8081 /
 * MinIO :9000 + 真实 PostgreSQL);dev server 由 playwright.real.config.ts
 * 拉起并指向真实后端。浏览器以 --disable-web-security 启动(后端未开 CORS,
 * 生产经 nginx 同源反代;仅联调用途,见配置注释)。
 */
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const RUN = String(Date.now()).slice(-7);
const EMAIL = `attach-${RUN}@corp.example`;
const PASSWORD = 'S3cure-passw0rd!';
const SLUG = `att${RUN}`;
const API = process.env.MESH_E2E_API_BASE_URL ?? 'http://127.0.0.1:8000';
const EVIDENCE_DIR = process.env.MES59_EVIDENCE_DIR ?? resolve(HERE, 'evidence', 'attachments');
const PNG_PATH = resolve(HERE, 'fixtures', 'mesh-upload.png');
const PNG_BYTES = readFileSync(PNG_PATH);

test.describe.configure({ mode: 'serial' });

let token = '';
let workspaceId = '';
let issueId = '';

async function registerAndLogin(page: Page): Promise<void> {
  await page.goto('/login');
  await page.getByTestId('login-mode-register').click();
  await page.getByTestId('login-display-name').fill('Attach Owner');
  await page.getByTestId('login-email').fill(EMAIL);
  await page.getByTestId('login-password').fill(PASSWORD);
  await page.getByTestId('login-account-submit').click();
  // 注册即自动登录。结果页(「验证邮件已发送」)与会话写入存在同步竞争
  // (LoginPage 守卫镜像,负载高时偶发先跳应用),两种落点都接受:
  // 等 localStorage 出现会话 token,再按当前界面分支继续。
  await page.waitForFunction(
    () => {
      for (let i = 0; i < localStorage.length; i += 1) {
        const raw = localStorage.getItem(localStorage.key(i) ?? '');
        if (raw && raw.includes('"token"') && raw.includes('eyJ')) return true;
      }
      return false;
    },
    undefined,
    { timeout: 30_000 },
  );
  if (await page.getByTestId('register-continue').isVisible().catch(() => false)) {
    await page.getByTestId('register-continue').click();
  }
  // 若竞争落回登录页,直接进应用(会话已建立)。
  if (page.url().includes('/login')) {
    await page.goto('/');
  }
  await expect(page.getByText('Connected')).toBeVisible({ timeout: 30_000 });
}

async function apiJson(method: string, path: string, body?: unknown, authToken?: string): Promise<Record<string, unknown>> {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`API ${method} ${path} -> ${res.status}: ${text.slice(0, 300)}`);
  return text ? (JSON.parse(text) as Record<string, unknown>) : {};
}

test('MES-59 attachment UI walkthrough (real backend + real MinIO)', async ({ page }) => {
  test.setTimeout(300_000);
  mkdirSync(EVIDENCE_DIR, { recursive: true });

  // ---------- 1. 注册 / 登录(UI)----------
  await registerAndLogin(page);
  token = await page.evaluate(() => {
    for (let i = 0; i < localStorage.length; i += 1) {
      const raw = localStorage.getItem(localStorage.key(i) ?? '');
      if (raw && raw.includes('"token"')) {
        try {
          const state = (JSON.parse(raw) as { state?: { token?: string } }).state;
          if (state?.token) return state.token;
        } catch { /* keep looking */ }
      }
    }
    return '';
  });
  expect(token.length).toBeGreaterThan(0);

  // ---------- 2. 脚手架:workspace + issue(真实 API)----------
  const ws = (await apiJson('POST', '/api/v1/workspaces', { name: 'Attach WS', slug: SLUG }, token))
    .data as { id: string };
  workspaceId = ws.id;
  const statuses = (await apiJson('GET', `/api/v1/workspaces/${workspaceId}/statuses`, undefined, token))
    .data as Array<{ id: string }>;
  const issue = (await apiJson(
    'POST',
    `/api/v1/workspaces/${workspaceId}/issues`,
    { title: 'Attachment walkthrough', status_id: statuses[0].id },
    token,
  )).data as { id: string };
  issueId = issue.id;

  // ---------- 3. issue 详情:空附件区 + composer 上传入口(§4.1)----------
  await page.goto(`/issues/${issueId}`);
  await expect(page.getByTestId('attachments-empty')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId('attachment-composer')).toBeVisible();
  await page.screenshot({ path: resolve(EVIDENCE_DIR, `01-empty-panel-${RUN}.png`) });

  // ---------- 4. 三阶段直传:UI 选择文件 → 进度卡片 → complete ----------
  await page.getByTestId('attachment-file-input').setInputFiles(PNG_PATH);
  await expect(page.getByTestId('upload-cards')).toBeVisible({ timeout: 30_000 });
  await page.screenshot({ path: resolve(EVIDENCE_DIR, `02-upload-progress-${RUN}.png`) });
  const confirm = page.getByTestId('attachment-submit');
  await expect(confirm).toBeEnabled({ timeout: 60_000 });
  await confirm.click();

  // ---------- 5. 可见性闸门(T14 UI 态):扫描中占位,不暴露下载 ----------
  // complete 后面板即刻重取:此刻 blob 尚在隔离区(worker 放行需 ~1s+),
  // 占位呈现「扫描中」且无下载按钮;随后 attachment.processed 实时合并为缩略图。
  const anyScanning = page.locator('[data-testid^="attachment-scanning-"]');
  if (await anyScanning.first().isVisible({ timeout: 5_000 }).catch(() => false)) {
    await expect(page.locator('[data-testid^="attachment-download-"]')).toHaveCount(0);
    await page.screenshot({ path: resolve(EVIDENCE_DIR, `03-scanning-gate-${RUN}.png`) });
  }

  // ---------- 6. worker 放行:缩略图经实时帧出现(§4.3)----------
  const thumb = page.locator('[data-testid^="attachment-thumb-"]').first();
  await expect(thumb).toBeVisible({ timeout: 90_000 });
  await expect(thumb.locator('img')).toHaveAttribute('src', /^http/, { timeout: 60_000 });
  await page.waitForFunction(() => {
    const img = document.querySelector('[data-testid^="attachment-thumb-"] img') as HTMLImageElement | null;
    return img !== null && img.complete && img.naturalWidth > 0;
  }, undefined, { timeout: 60_000 });
  await page.screenshot({ path: resolve(EVIDENCE_DIR, `04-image-released-${RUN}.png`) });

  // ---------- 7. 灯箱:缩放 / 旋转 / 重置 / 定位 / 下载(§4.3)----------
  await thumb.click();
  await expect(page.getByTestId('lightbox-image')).toBeVisible({ timeout: 30_000 });
  const lightboxImg = page.getByTestId('lightbox-image');
  await page.waitForFunction(() => {
    const img = document.querySelector('[data-testid="lightbox-image"]') as HTMLImageElement | null;
    return img !== null && img.complete && img.naturalWidth > 0;
  }, undefined, { timeout: 30_000 });
  await expect(lightboxImg).toHaveCSS('transform', /matrix/);
  await page.getByTestId('lightbox-zoom-in').click();
  await expect(lightboxImg).toHaveCSS('transform', 'matrix(1.5, 0, 0, 1.5, 0, 0)');
  await page.getByTestId('lightbox-rotate').click();
  await page.getByTestId('lightbox-reset').click();
  await expect(lightboxImg).toHaveCSS('transform', 'matrix(1, 0, 0, 1, 0, 0)');
  await page.screenshot({ path: resolve(EVIDENCE_DIR, `05-lightbox-controls-${RUN}.png`) });
  const [download] = await Promise.all([
    page.waitForEvent('download', { timeout: 30_000 }),
    page.getByTestId('lightbox-download').click(),
  ]);
  const lightboxSaved = resolve(EVIDENCE_DIR, `lightbox-download-${RUN}.png`);
  await download.saveAs(lightboxSaved);
  expect(readFileSync(lightboxSaved).equals(PNG_BYTES)).toBe(true);
  // 在附件区定位(§4.3):关闭灯箱并回到附件条目。
  await page.getByTestId('lightbox-locate').click();
  await expect(page.getByTestId('lightbox-image')).toHaveCount(0);

  // ---------- 8. 秒传 T24:同 hash 已可读 → 免传;他人探测 → 不得短路 ----------
  const probe = (await apiJson(
    'POST',
    '/api/v1/attachments/upload-requests',
    {
      workspace_id: workspaceId,
      file_name: 'instant.png',
      file_size: PNG_BYTES.length,
      mime_type: 'image/png',
      content_hash: await sha256Hex(PNG_BYTES),
      link_to: { type: 'issue', id: issueId },
    },
    token,
  )).data as { upload: unknown; id: string };
  expect(probe.upload).toBeNull(); // possession 成立 → 跳过字节直传

  // 另一个工作区的凭据探测同一 hash:必须签发完整上传(不得凭 hash 短路)。
  const otherEmail = `attach-other-${RUN}@corp.example`;
  await apiJson('POST', '/api/v1/auth/register', {
    email: otherEmail,
    password: PASSWORD,
    display_name: 'Other Probe',
  });
  const otherLogin = (await apiJson('POST', '/api/v1/auth/login', { email: otherEmail, password: PASSWORD }))
    .data as { access_token: string };
  const otherWs = (await apiJson('POST', '/api/v1/workspaces', { name: 'Other WS', slug: `atto${RUN}` }, otherLogin.access_token))
    .data as { id: string };
  const foreign = (await apiJson(
    'POST',
    '/api/v1/attachments/upload-requests',
    {
      workspace_id: otherWs.id,
      file_name: 'steal.png',
      file_size: PNG_BYTES.length,
      mime_type: 'image/png',
      content_hash: await sha256Hex(PNG_BYTES),
    },
    otherLogin.access_token,
  )).data as { upload: { method?: string } | null };
  expect(foreign.upload).not.toBeNull(); // RED LINE:无 possession 强制完整上传
  expect(foreign.upload?.method).toBe('PUT');

  // 秒传附件经实时/重取并入网格(共享同一 blob,独立记录)。
  await page.goto(`/issues/${issueId}`);
  await expect(page.locator('[data-testid^="attachment-thumb-"]')).toHaveCount(2, { timeout: 60_000 });
  await page.screenshot({ path: resolve(EVIDENCE_DIR, `06-instant-dedup-${RUN}.png`) });

  // ---------- 9. 文本文件 → 文件卡片;删除 → 乐观移除 ----------
  const txtPath = resolve(EVIDENCE_DIR, `notes-${RUN}.txt`);
  writeFileSync(txtPath, 'sprint notes\n- ship attachments\n- verify quarantine\n');
  await page.getByTestId('attachment-file-input').setInputFiles(txtPath);
  await expect(page.getByTestId('attachment-submit')).toBeEnabled({ timeout: 60_000 });
  await page.getByTestId('attachment-submit').click();
  const fileCard = page.locator('li[data-testid^="attachment-file-"]').first();
  await expect(fileCard).toBeVisible({ timeout: 60_000 });
  await page.screenshot({ path: resolve(EVIDENCE_DIR, `07-file-card-${RUN}.png`) });

  const cardTestId = (await fileCard.getAttribute('data-testid')) ?? '';
  const cardId = cardTestId.replace('attachment-file-', '');
  await page.getByTestId(`attachment-delete-${cardId}`).click();
  await expect(page.getByTestId(`attachment-file-${cardId}`)).toHaveCount(0);
  await page.screenshot({ path: resolve(EVIDENCE_DIR, `08-after-delete-${RUN}.png`) });
});

async function sha256Hex(bytes: Buffer): Promise<string> {
  const { createHash } = await import('node:crypto');
  return createHash('sha256').update(bytes).digest('hex');
}
