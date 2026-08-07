/**
 * 技能详情页组件测试:五 Tab + 安装 + 状态变更 + 启停。
 */
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { RealtimeContext } from '../../../shell/AppShell';
import type { RealtimeContextValue } from '../../../shell/AppShell';
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

const AGENT_MEMBERS = [
  {
    id: 'ag-1',
    member_type: 'agent',
    role: 'member',
    status: 'active',
    display_name: 'Planner',
    joined_at: null,
    profile: null,
  },
  {
    id: 'ag-2',
    member_type: 'agent',
    role: 'member',
    status: 'active',
    display_name: 'Coder',
    joined_at: null,
    profile: null,
  },
];

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
    if (url.includes('/members'))
      return fakeResponse({ body: { data: AGENT_MEMBERS, next_cursor: null } });
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
  it('无工作区与成员查询失败时显示对应状态', async () => {
    const noWorkspaceFetch = (async (input: RequestInfo | URL) => {
      if (String(input).includes('/users/me')) {
        return fakeResponse({ body: { data: { ...ME, memberships: [] } } });
      }
      return fakeResponse({ body: { data: [] } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', noWorkspaceFetch);
    const noWorkspace = renderPage();
    expect(
      await screen.findByText(/not a member of any workspace|还不是任何工作区的成员/i),
    ).toBeTruthy();
    noWorkspace.unmount();

    const failedMembershipFetch = (async () =>
      fakeResponse({
        status: 500,
        body: { error: { code: 'internal_error', message: 'x' } },
      })) as typeof fetch;
    vi.stubGlobal('fetch', failedMembershipFetch);
    renderPage();
    expect(await screen.findByText(/Could not load skills|技能加载失败/i)).toBeTruthy();
  });

  it('渲染概览 + 含脚本标记 + 右侧详情', async () => {
    setup();
    renderPage();
    expect(await screen.findByTestId('skill-detail-name')).toHaveTextContent('发布检查清单');
    expect(screen.getByText(/含脚本|Contains scripts/)).toBeTruthy();
    expect(screen.getByTestId('skill-install')).toBeTruthy();
    expect(screen.getByTestId('skill-side-actions').querySelector('h2')).not.toBeNull();
    expect(screen.getByTestId('skill-side-actions').querySelector('h3')).toBeNull();
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

  it('安装 + 启停 + 状态变更发起请求', async () => {
    const { calls } = setup();
    renderPage();
    await screen.findByTestId('skill-install');
    fireEvent.click(screen.getByTestId('skill-install'));
    fireEvent.click(screen.getByTestId('skill-disable-button'));
    fireEvent.change(screen.getByTestId('skill-lifecycle-select'), {
      target: { value: 'deprecated' },
    });
    await waitFor(() => {
      expect(calls.some((c) => c.method === 'POST' && c.url.includes('/skill-installations'))).toBe(
        true,
      );
      expect(
        calls.some((c) => c.method === 'PATCH' && c.url.includes('/skill-installations')),
      ).toBe(true);
      expect(calls.some((c) => c.method === 'PATCH' && c.url.endsWith('/skills/s-1'))).toBe(true);
    });
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
    fireEvent.click(await screen.findByTestId('skill-enable-button'));
    await waitFor(() => expect(screen.getByTestId('skill-detail')).toBeTruthy());
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
    const diff = await screen.findByTestId('skill-diff-view');
    expect(diff.querySelector('h2')).not.toBeNull();
    expect(diff.querySelector('h4')).toBeNull();
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
    fireEvent.click(screen.getByTestId('skill-update-later'));
    fireEvent.click(screen.getByTestId('skill-update-now'));
    await waitFor(() =>
      expect(
        calls.some((c) => c.method === 'PATCH' && c.url.includes('/skill-installations')),
      ).toBe(true),
    );
  });

  it('回滚/更新/安装/停用 失败走 catch 分支', async () => {
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
    // install catch
    fireEvent.click(await screen.findByTestId('skill-install'));
    // rollback catch
    fireEvent.click(screen.getByTestId('skill-tab-versions'));
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

  it('加载失败呈现错误态', async () => {
    const impl = (async (input: RequestInfo | URL) => {
      if (String(input).includes('/users/me')) return fakeResponse({ body: { data: ME } });
      return fakeResponse({
        status: 500,
        body: { error: { code: 'internal_error', message: 'x' } },
      });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    expect(await screen.findByText('Something went wrong')).toBeTruthy();
  });

  it('空选择与稀疏元数据走安全回退,脚本对象能力/正文引用均可读', async () => {
    const sparseSkill = {
      ...SKILL,
      source_type: null,
      trust_level: null,
      status: 'deprecated',
      has_scripts: false,
      required_capabilities: [{ capability: 'read:issues' }],
      tags: [],
    };
    const sparseVersion = {
      ...VERSION,
      changelog: null,
      scripts: [
        {
          ...VERSION.scripts[0],
          entrypoint: false,
          content: null,
          required_capabilities: [{ capability: 'read:issues' }],
        },
      ],
      references: [{ ...VERSION.references[0], summary: null }],
    };
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/versions/v-1')) return fakeResponse({ body: { data: sparseVersion } });
      if (url.includes('/versions')) {
        return fakeResponse({ body: { data: [sparseVersion], next_cursor: null } });
      }
      if (url.includes('/skill-installations')) {
        return fakeResponse({ body: { data: [INSTALLATION], next_cursor: null } });
      }
      return fakeResponse({ body: { data: sparseSkill } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    await screen.findByTestId('skill-detail-name');

    fireEvent.click(screen.getByTestId('skill-tab-scripts'));
    expect(await screen.findByText('Pick a version')).toBeTruthy();
    fireEvent.click(screen.getByTestId('skill-tab-versions'));
    fireEvent.click(await screen.findByTestId('skill-view-1.0.0'));
    const scripts = await screen.findByTestId('skill-panel-scripts');
    expect(scripts.textContent).toContain('mem:x');
    expect(scripts.textContent).toContain('read:issues');
    expect(scripts.textContent).not.toContain('entrypoint');

    fireEvent.click(screen.getByTestId('skill-tab-references'));
    expect(screen.getByTestId('skill-panel-references').textContent).toContain('docs/r.md');
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(2);
    const lifecycle = screen.getByTestId('skill-lifecycle-select') as HTMLSelectElement;
    expect(Array.from(lifecycle.options).map((option) => option.value)).toContain('disabled');
  });

  it('disabled 生命周期提供恢复/弃用动作', async () => {
    const disabledSkill = { ...SKILL, status: 'disabled' };
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/versions')) {
        return fakeResponse({ body: { data: [VERSION], next_cursor: null } });
      }
      if (url.includes('/skill-installations')) {
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }
      return fakeResponse({ body: { data: disabledSkill } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    const lifecycle = (await screen.findByTestId('skill-lifecycle-select')) as HTMLSelectElement;
    expect(Array.from(lifecycle.options).map((option) => option.value)).toEqual([
      '',
      'published',
      'deprecated',
    ]);
  });

  it('复杂行 diff 覆盖删除/增加/相同与收尾分支,再次点击收起', async () => {
    const current = {
      ...VERSION,
      id: 'v-current',
      version: '2.0.0',
      instructions: 'shared\ntrailing-new',
      is_current: true,
    };
    const historic = {
      ...VERSION,
      id: 'v-old',
      version: '1.0.0',
      instructions: 'leading-old\nshared',
      is_current: false,
    };
    const alternate = {
      ...VERSION,
      id: 'v-alternate',
      version: '0.9.0',
      instructions: 'trailing-new\ntrailing-old',
      is_current: false,
    };
    const impl = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (url.includes('/versions')) {
        return fakeResponse({ body: { data: [current, historic, alternate], next_cursor: null } });
      }
      if (url.includes('/skill-installations')) {
        return fakeResponse({ body: { data: [INSTALLATION], next_cursor: null } });
      }
      return fakeResponse({
        body: { data: { ...SKILL, current_version_id: 'v-current', current_version: '2.0.0' } },
      });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    fireEvent.click(await screen.findByTestId('skill-tab-versions'));
    const toggle = await screen.findByTestId('skill-diff-1.0.0');
    fireEvent.click(toggle);
    const diff = await screen.findByTestId('skill-diff-view');
    expect(diff.querySelector('.mesh-skills-detail__diff-del')).toBeTruthy();
    expect(diff.querySelector('.mesh-skills-detail__diff-add')).toBeTruthy();
    expect(diff.querySelector('.mesh-skills-detail__diff-eq')).toBeTruthy();
    fireEvent.click(toggle);
    expect(screen.queryByTestId('skill-diff-view')).toBeNull();

    fireEvent.click(screen.getByTestId('skill-diff-0.9.0'));
    expect(await screen.findByTestId('skill-diff-view')).toBeTruthy();
  });

  it('realtime 同频道帧触发重拉,无关频道忽略,卸载时退订', async () => {
    const { calls } = setup();
    const handlers: Array<(frame: unknown) => void> = [];
    const realtimeClient = {
      subscribe: vi.fn(),
      unsubscribe: vi.fn(),
      onFrame: vi.fn((handler: (frame: unknown) => void) => {
        handlers.push(handler);
        return vi.fn();
      }),
    };
    const realtime: RealtimeContextValue = {
      state: 'connected',
      client: realtimeClient as never,
    };
    const rendered = renderWithProviders(
      <RealtimeContext.Provider value={realtime}>
        <Routes>
          <Route path="/skills/:skillId" element={<SkillDetailPage />} />
        </Routes>
      </RealtimeContext.Provider>,
      { route: '/skills/s-1' },
    );
    await screen.findByTestId('skill-detail-name');
    const initial = calls.filter(
      (call) => call.method === 'GET' && call.url.includes('/skills/s-1'),
    ).length;
    act(() => {
      handlers[0]({ channel: 'workspace:other:skills' });
      handlers[0]({ channel: 'workspace:ws-1:skills' });
    });
    await waitFor(() =>
      expect(
        calls.filter((call) => call.method === 'GET' && call.url.includes('/skills/s-1')).length,
      ).toBeGreaterThan(initial),
    );
    rendered.unmount();
    expect(realtimeClient.unsubscribe).toHaveBeenCalledWith('workspace:ws-1:skills');
  });

  it('bulk-bind 按钮打开对话框,确认后 POST skills/bulk-bind(L247)', async () => {
    const calls: { url: string; method: string; body: unknown }[] = [];
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ url, method, body });
      if (url.includes('/users/me')) return fakeResponse({ body: { data: ME } });
      if (method === 'POST' && url.includes('/bulk-bind'))
        return fakeResponse({ body: { data: { bound: [{ binding_id: 'b-1' }], errors: [] } } });
      if (url.includes('/versions/v-1')) return fakeResponse({ body: { data: VERSION } });
      if (url.includes('/versions'))
        return fakeResponse({ body: { data: [VERSION], next_cursor: null } });
      if (url.includes('/skill-installations'))
        return fakeResponse({ body: { data: [INSTALLATION], next_cursor: null } });
      if (url.includes('/members'))
        return fakeResponse({ body: { data: AGENT_MEMBERS, next_cursor: null } });
      return fakeResponse({ body: { data: SKILL } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPage();
    await screen.findByTestId('skill-detail-name');

    fireEvent.click(await screen.findByTestId('skill-bulk-bind-open'));
    await screen.findByTestId('bulk-bind-body');
    fireEvent.click(screen.getByTestId('bulk-bind-select-all'));
    fireEvent.click(screen.getByTestId('bulk-bind-confirm'));

    await waitFor(() =>
      expect(calls.some((c) => c.method === 'POST' && c.url.includes('/skills/bulk-bind'))).toBe(
        true,
      ),
    );
    const post = calls.find((c) => c.method === 'POST' && c.url.includes('/skills/bulk-bind'));
    expect(post?.body).toEqual({
      skill_installation_id: 'i-1',
      agent_ids: ['ag-1', 'ag-2'],
    });
    expect(await screen.findByText('Bulk bind: 1 succeeded, 0 failed')).toBeTruthy();
  });
});
