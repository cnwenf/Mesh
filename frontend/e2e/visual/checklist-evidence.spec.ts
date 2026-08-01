/**
 * competitor-parity-checklist §3 的四组合存证补齐。
 *
 * 该用例只生成现有存证集未覆盖的页面；每张图在截图前都等待页面
 * 专属的 ready selector，因此 manifest 不会根据文件名猜测页面。这些是浏览器 +
 * mock contract 存证，不声称真实数据库落库。
 */
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { expect, test } from '@playwright/test';
import type { Page, Route } from '@playwright/test';
import { prepareVisualPage, waitForStable } from './visual-helpers';

const THEMES = ['light', 'dark'] as const;
const EVIDENCE_DIR = resolve('e2e/evidence/mes128-checklist');
const FIXED_TIME = '2026-07-25T08:00:00.000Z';

interface ChecklistPage {
  readonly row: number;
  readonly key: string;
  readonly path: string;
  readonly install?: (page: Page) => Promise<void>;
  readonly ready: (page: Page) => Promise<void>;
  readonly interact?: (page: Page, compact: boolean) => Promise<void>;
}

function listEnvelope(data: readonly unknown[]): Record<string, unknown> {
  return { data, next_cursor: null };
}

function singleEnvelope(data: unknown): Record<string, unknown> {
  return { data };
}

async function fulfillJson(route: Route, json: unknown): Promise<void> {
  await route.fulfill({
    status: 200,
    contentType: 'application/json; charset=utf-8',
    headers: { 'access-control-allow-origin': '*' },
    json,
  });
}

async function routeGet(page: Page, pathname: string, json: unknown): Promise<void> {
  await page.route('**/api/v1/**', async (route, request) => {
    const url = new URL(request.url());
    if (request.method() === 'GET' && url.pathname === pathname) {
      await fulfillJson(route, json);
      return;
    }
    await route.fallback();
  });
}

const PROJECT = {
  id: 'project-1',
  workspace_id: 'ws-1',
  name: '平台发布',
  key: 'PLAT',
  description: '跨团队发布计划与交付跟踪',
  icon: null,
  color: '#2563eb',
  status: 'published',
  health: 'on_track',
  visibility: 'public',
  lead: { id: 'member-human-1', name: 'Ana', member_type: 'human' },
  lead_member_id: 'member-human-1',
  start_date: '2026-07-01',
  target_date: '2026-08-31',
  progress: 0.6,
  open_issues: 2,
  done_issues: 3,
  issue_seq: 5,
  archived: false,
  archived_at: null,
  my_role: 'lead',
  created_at: FIXED_TIME,
  updated_at: FIXED_TIME,
};

const CYCLE = {
  id: 'cycle-1',
  project_id: 'project-1',
  name: '三季度发布',
  starts_at: '2026-07-20',
  ends_at: '2026-08-03',
  state: 'active',
  auto_roll: true,
  created_at: FIXED_TIME,
  updated_at: FIXED_TIME,
};

const SKILL = {
  id: 'skill-1',
  workspace_id: 'ws-1',
  source_id: 'source-1',
  source_type: 'builtin',
  trust_level: 'verified',
  name: '代码评审',
  slug: 'code-review',
  summary: '以可执行建议审查代码变更',
  status: 'published',
  current_version_id: 'skill-version-1',
  current_version: '1.2.0',
  has_scripts: true,
  install_status: 'installed',
  required_capabilities: ['version_control'],
  tags: ['engineering', 'quality'],
  icon: null,
  created_by: 'user-1',
  created_at: FIXED_TIME,
  updated_at: FIXED_TIME,
};

const SQUAD = {
  id: 'squad-1',
  workspace_id: 'ws-1',
  name: '发布小队',
  description: '联合完成产品发布与质量保障',
  instructions: '优先交付可验证的小步骤',
  avatar_url: null,
  kind: 'standing',
  status: 'active',
  leader_mode: 'single',
  primary_leader_id: 'member-agent-1',
  primary_leader: { id: 'member-agent-1', name: 'Mesh Agent', member_type: 'agent' },
  require_plan_approval: true,
  max_decompose_depth: 3,
  member_count: 3,
  active_task_count: 2,
  leaders: [{ id: 'member-agent-1', name: 'Mesh Agent', member_type: 'agent' }],
  member_preview: [
    { member_id: 'member-agent-1', member_type: 'agent', name: 'Mesh Agent', role: 'leader' },
    { member_id: 'member-human-1', member_type: 'human', name: 'Ana', role: 'member' },
  ],
  archived_at: null,
  created_at: FIXED_TIME,
  updated_at: FIXED_TIME,
};

const RUNTIME = {
  id: 'runtime-1',
  name: '构建节点 A',
  kind: 'self_hosted',
  status: 'online',
  labels: { region: 'intranet', gpu: 'false' },
  capabilities: ['version_control', 'python'],
  hostname: 'runner-a.internal',
  os: 'linux',
  cpu_cores: 8,
  memory_mb: 16384,
  max_concurrent: 4,
  current_load: 2,
  last_heartbeat_at: '2026-07-25T07:59:45.000Z',
  heartbeat_interval_seconds: 30,
  version: '1.4.0',
  created_at: FIXED_TIME,
  updated_at: FIXED_TIME,
};

const DATA_JOB = {
  id: 'job-1',
  workspace_id: 'ws-1',
  kind: 'import',
  entity_type: 'issues',
  format: 'csv',
  status: 'completed',
  total_rows: 120,
  succeeded_rows: 118,
  failed_rows: 2,
  source_attachment_id: 'source-attachment-1',
  result_attachment_id: 'result-attachment-1',
  failure_reason: null,
  requested_by: 'user-1',
  mapping: { title: 'title', description: 'description' },
  params: { locale: 'zh-CN' },
  started_at: FIXED_TIME,
  finished_at: '2026-07-25T08:01:00.000Z',
  created_at: FIXED_TIME,
  updated_at: '2026-07-25T08:01:00.000Z',
};

const ONBOARDING = {
  id: 'onboarding-1',
  workspace_id: 'ws-1',
  member_id: 'member-human-1',
  checklist: 'activation',
  aha_reached_at: null,
  dismissed_at: null,
  progress: { total: 5, completed: 2, skipped: 0 },
  steps: [
    {
      step_key: 'create_workspace',
      status: 'completed',
      completed_via: 'auto',
      completed_at: FIXED_TIME,
    },
    {
      step_key: 'invite_member_or_add_agent',
      status: 'completed',
      completed_via: 'manual',
      completed_at: FIXED_TIME,
    },
    {
      step_key: 'create_first_issue',
      status: 'pending',
      completed_via: null,
      completed_at: null,
    },
    {
      step_key: 'dispatch_or_mention_agent',
      status: 'pending',
      completed_via: null,
      completed_at: null,
    },
    {
      step_key: 'see_agent_reply_in_inbox',
      status: 'pending',
      completed_via: null,
      completed_at: null,
    },
  ],
  created_at: FIXED_TIME,
  updated_at: FIXED_TIME,
};

const ATTACHMENT = {
  id: 'img-1',
  blob_id: 'blob-1',
  file_name: 'release-board.png',
  file_size: 1432,
  mime_type: 'image/png',
  extension: 'png',
  is_image: true,
  image_width: 640,
  image_height: 360,
  scan_status: 'clean',
  upload_status: 'completed',
  uploader: { id: 'member-human-1', member_type: 'human', display_name: 'Ana' },
  links: [{ type: 'issue', id: 'issue-1', display: 'inline', position: 0 }],
  thumbnail_url: '/api/v1/attachments/img-1/thumbnail?size=md',
  download_url: '/api/v1/attachments/img-1/download',
  created_at: FIXED_TIME,
  updated_at: FIXED_TIME,
};

const CHECKLIST_PAGES: readonly ChecklistPage[] = [
  {
    row: 2,
    key: 'device',
    path: '/device?user_code=MESH-2026',
    install: async (page) => {
      await routeGet(
        page,
        '/api/v1/auth/device',
        singleEnvelope({
          client_name: 'Mesh CLI',
          requested_scopes: [
            { scope: 'issues:read', description: 'Read assigned issues' },
            { scope: 'comments:write', description: 'Post issue comments' },
          ],
          workspaces: [{ id: 'ws-1', slug: 'acme', name: 'Acme', my_role: 'owner' }],
        }),
      );
    },
    ready: async (page) => {
      await page.locator('#device-code').waitFor({ state: 'visible' });
      await page.getByText('Mesh CLI', { exact: true }).waitFor({ state: 'visible' });
    },
  },
  {
    row: 4,
    key: 'app-shell',
    path: '/',
    ready: async (page) => {
      await page.getByTestId('home-dashboard').waitFor({ state: 'visible' });
    },
    interact: async (page, compact) => {
      if (compact) {
        await page.getByTestId('mobile-nav-more').click();
        await page.getByRole('dialog').waitFor({ state: 'visible' });
      } else {
        await page.getByTestId('ws-switcher-button').click();
        await page.getByTestId('ws-switcher-dialog').waitFor({ state: 'visible' });
      }
    },
  },
  {
    row: 7,
    key: 'projects',
    path: '/projects',
    install: async (page) => {
      await routeGet(page, '/api/v1/workspaces/ws-1/projects', listEnvelope([PROJECT]));
    },
    ready: async (page) => {
      await page.getByTestId('project-card-project-1').waitFor({ state: 'visible' });
    },
  },
  {
    row: 8,
    key: 'cycles',
    path: '/cycles',
    install: async (page) => {
      await routeGet(page, '/api/v1/workspaces/ws-1/cycles', listEnvelope([CYCLE]));
    },
    ready: async (page) => {
      await page.getByTestId('cycle-row-cycle-1').waitFor({ state: 'visible' });
    },
  },
  {
    row: 14,
    key: 'skills',
    path: '/skills',
    install: async (page) => {
      await routeGet(page, '/api/v1/workspaces/ws-1/skills', listEnvelope([SKILL]));
    },
    ready: async (page) => {
      await page.getByTestId('skill-card-skill-1').waitFor({ state: 'visible' });
    },
  },
  {
    row: 16,
    key: 'squads',
    path: '/squads',
    install: async (page) => {
      await routeGet(page, '/api/v1/workspaces/ws-1/squads', listEnvelope([SQUAD]));
    },
    ready: async (page) => {
      await page.getByTestId('squad-card-squad-1').waitFor({ state: 'visible' });
    },
  },
  {
    row: 17,
    key: 'runtimes',
    path: '/runtimes',
    install: async (page) => {
      await routeGet(page, '/api/v1/workspaces/ws-1/runtimes', listEnvelope([RUNTIME]));
    },
    ready: async (page) => {
      await page.getByTestId('runtime-row-runtime-1').waitFor({ state: 'visible' });
    },
  },
  {
    row: 22,
    key: 'data-management',
    path: '/w/acme/settings/data',
    install: async (page) => {
      await routeGet(page, '/api/v1/data-jobs', listEnvelope([DATA_JOB]));
    },
    ready: async (page) => {
      await page.getByTestId('job-row-job-1').waitFor({ state: 'visible' });
      await page.getByTestId('open-import-wizard').waitFor({ state: 'visible' });
    },
  },
  {
    row: 26,
    key: 'onboarding',
    path: '/',
    install: async (page) => {
      await routeGet(page, '/api/v1/onboarding/state', singleEnvelope(ONBOARDING));
    },
    ready: async (page) => {
      await page.getByTestId('onboarding-card').waitFor({ state: 'visible' });
      await page.getByTestId('onboarding-step-create_first_issue').waitFor({ state: 'visible' });
    },
  },
  {
    row: 27,
    key: 'attachment-lightbox',
    path: '/issues/issue-1',
    install: async (page) => {
      const image = await readFile(resolve('e2e/fixtures/mesh-upload.png'));
      await routeGet(page, '/api/v1/issues/issue-1/attachments', listEnvelope([ATTACHMENT]));
      await routeGet(
        page,
        '/api/v1/attachments/img-1/thumbnail',
        singleEnvelope({
          url: 'http://127.0.0.1:5199/checklist-image.png',
          size: 'md',
          expires_at: '2026-07-25T09:00:00.000Z',
        }),
      );
      await routeGet(
        page,
        '/api/v1/attachments/img-1/download',
        singleEnvelope({
          url: 'http://127.0.0.1:5199/checklist-image.png',
          file_name: 'release-board.png',
          expires_at: '2026-07-25T09:00:00.000Z',
        }),
      );
      await page.route('**/checklist-image.png', async (route) => {
        await route.fulfill({ status: 200, contentType: 'image/png', body: image });
      });
    },
    ready: async (page) => {
      await page.getByTestId('attachment-thumb-img-1').waitFor({ state: 'visible' });
    },
    interact: async (page) => {
      await page.getByTestId('attachment-thumb-img-1').click();
      await page.getByTestId('lightbox-image').waitFor({ state: 'visible' });
      await page.getByTestId('lightbox-zoom-in').waitFor({ state: 'visible' });
      await page.getByTestId('lightbox-rotate').waitFor({ state: 'visible' });
      await page.getByTestId('lightbox-download').waitFor({ state: 'visible' });
    },
  },
  {
    row: 28,
    key: 'not-found',
    path: '/checklist-route-not-found',
    ready: async (page) => {
      await page.getByTestId('notfound-home').waitFor({ state: 'visible' });
    },
  },
];

for (const checklistPage of CHECKLIST_PAGES) {
  for (const theme of THEMES) {
    test(`row ${checklistPage.row}: ${checklistPage.key} ${theme}`, async ({ page }, testInfo) => {
      test.skip(!['wide', 'phone'].includes(testInfo.project.name), 'only checklist viewports');
      const compact = testInfo.project.name === 'phone';

      if (checklistPage.install !== undefined) await checklistPage.install(page);
      await prepareVisualPage(page, theme);
      await page.goto(checklistPage.path, { waitUntil: 'domcontentloaded' });
      await checklistPage.ready(page);
      if (checklistPage.interact !== undefined) {
        await checklistPage.interact(page, compact);
      }
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      await waitForStable(page);

      const layout = compact ? 'mobile' : 'desktop';
      const row = String(checklistPage.row).padStart(2, '0');
      await page.screenshot({
        path: resolve(EVIDENCE_DIR, `${layout}-${row}-${checklistPage.key}-${theme}.png`),
        fullPage: false,
        animations: 'disabled',
        caret: 'hide',
      });
    });
  }
}
