/**
 * 技能详情页组件测试:五 Tab + 安装 + 状态变更 + 启停。
 */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { SkillDetailPage } from '../SkillDetailPage';

const SKILL = {
  id: 's-1',
  workspace_id: 'ws-1',
  source_id: 'src-1',
  source_type: 'url',
  trust_level: 'untrusted',
  name: '发布检查清单',
  slug: 'release-checklist',
  summary: '发布前检查',
  status: 'published',
  current_version_id: 'v-1',
  required_capabilities: ['exec:shell', 'net:outbound'],
  tags: ['release'],
  icon: null,
  created_by: 'm-1',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  current_version: '1.0.0',
  has_scripts: true,
};

const VERSION = {
  id: 'v-1',
  skill_id: 's-1',
  version: '1.0.0',
  instructions: '## 指令正文',
  status: 'published',
  changelog: 'init',
  io_contract: null,
  required_capabilities: ['exec:shell'],
  content_hash: 'a'.repeat(64),
  created_by: 'm-1',
  created_at: '2026-01-01T00:00:00Z',
  is_current: true,
  scripts: [
    {
      id: 'sc-1',
      path: 'scripts/check.sh',
      runtime: 'shell',
      entrypoint: true,
      content_ref: 'mem:x',
      content_hash: 'b'.repeat(64),
      required_capabilities: ['exec:shell', 'net:outbound'],
      content: '#!/bin/sh\necho ok',
    },
  ],
  references: [
    {
      id: 'rf-1',
      path: 'docs/r.md',
      media_type: 'text/markdown',
      content_ref: 'mem:y',
      summary: 'runbook',
    },
  ],
  triggers: [{ id: 'tr-1', trigger_type: 'keyword', pattern: '发布', weight: 1.5 }],
};

const INSTALLATION = {
  id: 'i-1',
  workspace_id: 'ws-1',
  skill_id: 's-1',
  skill_version_id: 'v-1',
  scope: 'workspace',
  agent_id: null,
  install_status: 'installed',
  auto_update: false,
  granted_capabilities: ['exec:shell'],
  installed_by: 'm-1',
  installed_at: '2026-01-01T00:00:00Z',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

const ME = {
  user: { id: 'u-1', email: 'o@x.com', display_name: 'Owner' },
  memberships: [
    {
      workspace_id: 'ws-1',
      workspace_name: 'T',
      workspace_slug: 't',
      role: 'owner',
      status: 'active',
      joined_at: null,
    },
  ],
};

function setup(): { calls: { url: string; method: string }[] } {
  const calls: { url: string; method: string }[] = [];
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method });
    if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
    if (url.includes('/versions/v-1')) return fakeResponse({ body: { data: VERSION } });
    if (url.includes('/versions'))
      return fakeResponse({ body: { data: [VERSION], next_cursor: null } });
    if (url.includes('/skill-installations'))
      return fakeResponse({ body: { data: [INSTALLATION], next_cursor: null } });
    if (url.includes('/skills/s-1')) return fakeResponse({ body: { data: SKILL } });
    return fakeResponse({ body: { data: SKILL } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return { calls };
}

function renderPage() {
  return renderWithProviders(
    <Routes>
      <Route path="/skills/:skillId" element={<SkillDetailPage />} />
    </Routes>,
    { route: '/skills/s-1' },
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('SkillDetailPage', () => {
  it('渲染概览 + 含脚本标记 + 右侧详情', async () => {
    setup();
    renderPage();
    expect(await screen.findByTestId('skill-detail-name')).toHaveTextContent('发布检查清单');
    expect(screen.getByText(/含脚本|Contains scripts/)).toBeTruthy();
    expect(screen.queryByTestId('skill-install')).toBeNull();
    const side = screen.getByTestId('skill-side-actions');
    expect(side).toHaveTextContent('exec:shell');
    expect(side).toHaveTextContent(/confirm|确认/i);
  });

  it('版本 Tab + 查看打开脚本 Tab', async () => {
    setup();
    renderPage();
    await screen.findByTestId('skill-detail-name');
    fireEvent.click(screen.getByTestId('skill-tab-versions'));
    await screen.findByTestId('skill-panel-versions');
    fireEvent.click(screen.getByTestId('skill-view-1.0.0'));
    await screen.findByTestId('skill-panel-scripts');
    expect(screen.getByText(/scripts\/check\.sh/)).toBeTruthy();
  });

  it('资料 + 触发条件 Tab', async () => {
    setup();
    renderPage();
    await screen.findByTestId('skill-detail-name');
    // open a version so references/triggers have data
    fireEvent.click(screen.getByTestId('skill-tab-versions'));
    await screen.findByTestId('skill-panel-versions');
    fireEvent.click(screen.getByTestId('skill-view-1.0.0'));
    await screen.findByTestId('skill-panel-scripts');
    fireEvent.click(screen.getByTestId('skill-tab-references'));
    expect(screen.getByText(/docs\/r\.md/)).toBeTruthy();
    fireEvent.click(screen.getByTestId('skill-tab-triggers'));
    // pattern text lives in the triggers list (the name also contains 发布, so scope it)
    expect(screen.getByTestId('skill-panel-triggers').textContent).toMatch(/发布/);
  });

  it('启停 + 状态变更发起请求', async () => {
    const { calls } = setup();
    renderPage();
    fireEvent.click(await screen.findByTestId('skill-disable-button'));
    fireEvent.change(screen.getByTestId('skill-lifecycle-select'), {
      target: { value: 'deprecated' },
    });
    await waitFor(() => {
      expect(
        calls.some((c) => c.method === 'PATCH' && c.url.includes('/skill-installations')),
      ).toBe(true);
      expect(calls.some((c) => c.method === 'PATCH' && c.url.endsWith('/skills/s-1'))).toBe(true);
    });
  });

  it('未安装时显示安装按钮并 POST 安装记录', async () => {
    const calls: { url: string; method: string }[] = [];
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      calls.push({ url, method });
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/versions')) {
        return fakeResponse({ body: { data: [VERSION], next_cursor: null } });
      }
      if (url.includes('/skill-installations')) {
        return method === 'POST'
          ? fakeResponse({ body: { data: INSTALLATION } })
          : fakeResponse({ body: { data: [], next_cursor: null } });
      }
      return fakeResponse({ body: { data: SKILL } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    fireEvent.click(await screen.findByTestId('skill-install'));
    await waitFor(() =>
      expect(
        calls.some((call) => call.method === 'POST' && call.url.endsWith('/skill-installations')),
      ).toBe(true),
    );
  });

  it('无已发布版本 → 概览空态且无安装按钮', async () => {
    const noVer = { ...SKILL, current_version_id: null, current_version: null, status: 'draft' };
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/versions')) return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/skill-installations'))
        return fakeResponse({ body: { data: [], next_cursor: null } });
      return fakeResponse({ body: { data: noVer } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    expect(await screen.findByTestId('skill-detail-name')).toBeTruthy();
    expect(screen.queryByTestId('skill-install')).toBeNull();
    expect(screen.getByTestId('skill-panel-overview')).toBeTruthy();
  });

  it('空资料/触发条件 → 空态', async () => {
    const emptyVer = { ...VERSION, references: [], triggers: [], scripts: [] };
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/versions/v-1')) return fakeResponse({ body: { data: emptyVer } });
      if (url.includes('/versions'))
        return fakeResponse({ body: { data: [emptyVer], next_cursor: null } });
      if (url.includes('/skill-installations'))
        return fakeResponse({ body: { data: [INSTALLATION], next_cursor: null } });
      return fakeResponse({ body: { data: SKILL } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    await screen.findByTestId('skill-detail-name');
    fireEvent.click(screen.getByTestId('skill-tab-versions'));
    await screen.findByTestId('skill-panel-versions');
    fireEvent.click(screen.getByTestId('skill-view-1.0.0'));
    await screen.findByTestId('skill-panel-scripts');
    fireEvent.click(screen.getByTestId('skill-tab-references'));
    expect(screen.getByTestId('skill-panel-references')).toBeTruthy();
    fireEvent.click(screen.getByTestId('skill-tab-triggers'));
    expect(screen.getByTestId('skill-panel-triggers')).toBeTruthy();
  });

  it('只读成员隐藏侧边操作', async () => {
    const meMember = { ...ME, memberships: [{ ...ME.memberships[0], role: 'member' }] };
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: meMember } });
      if (url.includes('/versions/v-1')) return fakeResponse({ body: { data: VERSION } });
      if (url.includes('/versions'))
        return fakeResponse({ body: { data: [VERSION], next_cursor: null } });
      if (url.includes('/skill-installations'))
        return fakeResponse({ body: { data: [INSTALLATION], next_cursor: null } });
      return fakeResponse({ body: { data: SKILL } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    await screen.findByTestId('skill-detail-name');
    expect(screen.queryByTestId('skill-install')).toBeNull();
    expect(screen.queryByTestId('skill-lifecycle-select')).toBeNull();
  });

  it('已停用安装显示启用按钮', async () => {
    const disabledInst = { ...INSTALLATION, install_status: 'disabled' };
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/versions/v-1')) return fakeResponse({ body: { data: VERSION } });
      if (url.includes('/versions'))
        return fakeResponse({ body: { data: [VERSION], next_cursor: null } });
      if (url.includes('/skill-installations'))
        return fakeResponse({ body: { data: [disabledInst], next_cursor: null } });
      return fakeResponse({ body: { data: SKILL } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    expect(await screen.findByTestId('skill-enable-button')).toBeTruthy();
  });

  it('版本表回滚按钮 → 调用回滚接口', async () => {
    const calls: { url: string; method: string }[] = [];
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      calls.push({ url, method });
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (method === 'POST' && url.includes('/rollback'))
        return fakeResponse({ body: { data: {} } });
      if (url.includes('/versions/v-1')) return fakeResponse({ body: { data: VERSION } });
      if (url.includes('/versions')) {
        const v2 = { ...VERSION, id: 'v-2', version: '1.1.0', is_current: true };
        const v1 = { ...VERSION, id: 'v-1', version: '1.0.0', is_current: false };
        return fakeResponse({ body: { data: [v2, v1], next_cursor: null } });
      }
      if (url.includes('/skill-installations'))
        return fakeResponse({ body: { data: [INSTALLATION], next_cursor: null } });
      return fakeResponse({ body: { data: SKILL } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    await screen.findByTestId('skill-tab-versions');
    fireEvent.click(screen.getByTestId('skill-tab-versions'));
    fireEvent.click(screen.getByTestId('skill-rollback-1.0.0'));
    await waitFor(() =>
      expect(calls.some((c) => c.method === 'POST' && c.url.includes('/rollback'))).toBe(true),
    );
  });

  it('diff 按钮展开差异视图', async () => {
    const impl = (async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/versions/v-1'))
        return fakeResponse({ body: { data: { ...VERSION, instructions: 'old line\nshared' } } });
      if (url.includes('/versions')) {
        const v2 = {
          ...VERSION,
          id: 'v-2',
          version: '1.1.0',
          is_current: true,
          instructions: 'new line\nshared',
        };
        const v1 = { ...VERSION, id: 'v-1', version: '1.0.0', is_current: false };
        return fakeResponse({ body: { data: [v2, v1], next_cursor: null } });
      }
      if (url.includes('/skill-installations'))
        return fakeResponse({ body: { data: [INSTALLATION], next_cursor: null } });
      return fakeResponse({ body: { data: SKILL } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    await screen.findByTestId('skill-tab-versions');
    fireEvent.click(screen.getByTestId('skill-tab-versions'));
    await screen.findByTestId('skill-version-1.0.0');
    fireEvent.click(screen.getByTestId('skill-diff-1.0.0'));
    expect(await screen.findByTestId('skill-diff-view')).toBeTruthy();
  });

  it('updated_available 显示立即更新/稍后', async () => {
    const calls: { url: string; method: string }[] = [];
    const updInst = { ...INSTALLATION, install_status: 'updated_available' };
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      calls.push({ url, method });
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/versions/v-1')) return fakeResponse({ body: { data: VERSION } });
      if (url.includes('/versions'))
        return fakeResponse({ body: { data: [VERSION], next_cursor: null } });
      if (url.includes('/skill-installations'))
        return fakeResponse({ body: { data: [updInst], next_cursor: null } });
      return fakeResponse({ body: { data: { ...SKILL, current_version_id: 'v-1' } } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    expect(await screen.findByTestId('skill-update-now')).toBeTruthy();
    fireEvent.click(screen.getByTestId('skill-update-now'));
    await waitFor(() =>
      expect(
        calls.some((c) => c.method === 'PATCH' && c.url.includes('/skill-installations')),
      ).toBe(true),
    );
  });

  it('回滚/更新/停用 失败走 catch 分支', async () => {
    const failMutations = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (method === 'GET') {
        if (url.includes('/versions/v-1')) return fakeResponse({ body: { data: VERSION } });
        if (url.includes('/versions')) {
          const v2 = { ...VERSION, id: 'v-2', version: '1.1.0', is_current: true };
          const v1 = { ...VERSION, id: 'v-1', version: '1.0.0', is_current: false };
          return fakeResponse({ body: { data: [v2, v1], next_cursor: null } });
        }
        if (url.includes('/skill-installations')) {
          return fakeResponse({
            body: {
              data: [{ ...INSTALLATION, install_status: 'updated_available' }],
              next_cursor: null,
            },
          });
        }
        return fakeResponse({ body: { data: SKILL } });
      }
      // every mutation fails → exercises every catch branch
      return fakeResponse({
        status: 500,
        body: { error: { code: 'internal_error', message: 'x' } },
      });
    }) as typeof fetch;
    vi.stubGlobal('fetch', failMutations);
    renderPage();
    // rollback catch
    fireEvent.click(await screen.findByTestId('skill-tab-versions'));
    await screen.findByTestId('skill-rollback-1.0.0');
    fireEvent.click(screen.getByTestId('skill-rollback-1.0.0'));
    // update-now catch (422/500 path)
    fireEvent.click(screen.getByTestId('skill-update-now'));
    // disable catch
    fireEvent.click(screen.getByTestId('skill-disable-button'));
    // lifecycle change catch
    fireEvent.change(screen.getByTestId('skill-lifecycle-select'), {
      target: { value: 'deprecated' },
    });
    await waitFor(() => expect(screen.getByTestId('skill-detail')).toBeTruthy());
  });

  it('安装失败走 catch 分支且保持未安装态', async () => {
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (method === 'POST' && url.endsWith('/skill-installations')) {
        return fakeResponse({
          status: 500,
          body: { error: { code: 'internal_error', message: 'x' } },
        });
      }
      if (url.includes('/versions')) {
        return fakeResponse({ body: { data: [VERSION], next_cursor: null } });
      }
      if (url.includes('/skill-installations')) {
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }
      return fakeResponse({ body: { data: SKILL } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    fireEvent.click(await screen.findByTestId('skill-install'));
    await waitFor(() => expect(screen.getByTestId('skill-install')).toBeTruthy());
  });
});
