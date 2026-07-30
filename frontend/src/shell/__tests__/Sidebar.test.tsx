/**
 * Sidebar — 分组侧栏(design-quality §4.1):四分组 + 折叠 rail + 图标 + 激活态。
 */
import { useState } from 'react';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../test-utils/render';
import { WorkspaceProvider, useWorkspace } from '../../workspace/WorkspaceProvider';
import type { WorkspaceRole } from '../../api/workspace';
import { Sidebar } from '../Sidebar';

const EXPECTED: ReadonlyArray<{ testid: string; label: string; href: string }> = [
  { testid: 'nav-home', label: 'Home', href: '/' },
  { testid: 'nav-inbox', label: 'Inbox', href: '/inbox' },
  { testid: 'nav-projects', label: 'Projects', href: '/projects' },
  { testid: 'nav-issues', label: 'Issues', href: '/issues' },
  { testid: 'nav-board', label: 'Board', href: '/board' },
  { testid: 'nav-cycles', label: 'Cycles', href: '/cycles' },
  { testid: 'nav-members', label: 'Members', href: '/members' },
  { testid: 'nav-skills', label: 'Skills', href: '/skills' },
  { testid: 'nav-squads', label: 'Squads', href: '/squads' },
  { testid: 'nav-chat', label: 'Chat', href: '/chat' },
  // 统一「待我审批」入口(README §6.10 / §3.4 规范深链)。
  { testid: 'nav-approvals', label: 'Approvals', href: '/approvals' },
  { testid: 'nav-autopilots', label: 'Autopilots', href: '/autopilots' },
  // 运行环境独立入口(§4.1:中文与自动值守区分,不再同名「自动化」)
  { testid: 'nav-runtimes', label: 'Runtimes', href: '/runtimes' },
  { testid: 'nav-insights', label: 'Insights', href: '/insights' },
  // 集成平台(integrations.md §4);无工作区上下文时为扁平路径(经迁移解析)。
  { testid: 'nav-integrations', label: 'Integrations', href: '/integrations' },
  { testid: 'nav-settings', label: 'Settings', href: '/settings' },
];

function renderSidebar(route: string = '/') {
  return renderWithProviders(<Sidebar collapsed={false} onToggleCollapsed={() => undefined} />, { route });
}

describe('Sidebar(分组 + 图标 + 激活态)', () => {
  it('渲染全部导航项(目录文案 + testid + href + 图标)', () => {
    renderSidebar();
    for (const { testid, label, href } of EXPECTED) {
      const link = screen.getByTestId(testid);
      expect(link.textContent).toBe(label);
      expect(link.getAttribute('href')).toBe(href);
      // 每项携带统一 SVG 图标(§7.1:导航禁 emoji/字符图标)
      expect(link.querySelector('svg')).not.toBeNull();
    }
  });

  it('四分组标题按 §4.1 呈现(工作/团队/运行/管理)', () => {
    renderSidebar();
    const titles = screen.getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent);
    expect(titles).toEqual(['Work', 'Team', 'Run', 'Admin']);
  });

  it('当前路由的导航项带激活样式,首页为精确匹配', () => {
    renderSidebar('/board');
    expect(screen.getByTestId('nav-board').className).toContain('mesh-sidebar__link--active');
    expect(screen.getByTestId('nav-home').className).not.toContain('mesh-sidebar__link--active');
  });

  it('选中视图路由 /views/{id} 下看板入口保持激活(§4.2 URL 同步)', () => {
    renderSidebar('/views/v1');
    expect(screen.getByTestId('nav-board').className).toContain('mesh-sidebar__link--active');
    expect(screen.getByTestId('nav-home').className).not.toContain('mesh-sidebar__link--active');
  });

  it('根路由下仅首页激活(精确匹配,不吞并子路由)', () => {
    renderSidebar('/');
    expect(screen.getByTestId('nav-home').className).toContain('mesh-sidebar__link--active');
    expect(screen.getByTestId('nav-inbox').className).not.toContain('mesh-sidebar__link--active');
  });
});

describe('Sidebar 折叠 rail(§4.1)', () => {
  it('折叠态:组标题隐藏、rail 类名生效、切换按钮 aria-expanded=false', () => {
    renderWithProviders(<Sidebar collapsed onToggleCollapsed={() => undefined} />);
    expect(screen.queryByRole('heading', { level: 2 })).toBeNull();
    const nav = screen.getByLabelText('Sidebar navigation');
    expect(nav.className).toContain('mesh-sidebar--collapsed');
    expect(screen.getByTestId('sidebar-toggle')).toHaveAttribute('aria-expanded', 'false');
  });

  it('展开态:组标题可见,切换按钮 aria-expanded=true,点击上抛切换回调', async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    renderWithProviders(<Sidebar collapsed={false} onToggleCollapsed={onToggle} />);
    expect(screen.getAllByRole('heading', { level: 2 })).toHaveLength(4);
    const toggle = screen.getByTestId('sidebar-toggle');
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await user.click(toggle);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('折叠态导航项可访问名保留(文字离屏不离可访问树)', () => {
    renderWithProviders(<Sidebar collapsed onToggleCollapsed={() => undefined} />);
    expect(screen.getByTestId('nav-inbox').textContent).toBe('Inbox');
  });
});

describe('Sidebar 折叠状态联动(AppShell 契约)', () => {
  it('受控折叠:状态翻转驱动 rail 类名', async () => {
    const user = userEvent.setup();
    function Harness(): React.JSX.Element {
      const [collapsed, setCollapsed] = useState(false);
      return <Sidebar collapsed={collapsed} onToggleCollapsed={() => setCollapsed((prev) => !prev)} />;
    }
    renderWithProviders(<Harness />);
    const nav = screen.getByLabelText('Sidebar navigation');
    expect(nav.className).not.toContain('mesh-sidebar--collapsed');
    await user.click(screen.getByTestId('sidebar-toggle'));
    expect(nav.className).toContain('mesh-sidebar--collapsed');
    expect(screen.queryByRole('heading', { level: 2 })).toBeNull();
  });
});

/** 工作区全量桩(与 WorkspaceProvider 测试同构:by-slug 返回 detail 信封) */
const WORKSPACE_DETAIL = {
  id: 'ws-1',
  name: 'Acme',
  slug: 'acme',
  logo_url: null,
  timezone: 'UTC',
  settings: { default_locale: 'en' },
  my_role: 'owner',
  created_at: '2026-07-25T00:00:00Z',
  updated_at: '2026-07-25T00:00:00Z',
};

function stubWorkspaceClient(myRole: WorkspaceRole): unknown {
  const fetchImpl = vi.fn().mockImplementation(() =>
    Promise.resolve(
      new Response(JSON.stringify({ data: { ...WORKSPACE_DETAIL, my_role: myRole } }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    ),
  );
  return {
    baseUrl: 'http://localhost',
    request: async (method: string, path: string): Promise<unknown> => {
      const response = (await fetchImpl(`http://localhost${path}`, { method })) as Response;
      const body = (await response.json()) as { data?: unknown };
      return body.data;
    },
  };
}

/** 上下文状态探针:断言「缺席」前先确认工作区已就绪,避免异步时序假判。 */
function WorkspaceStatusProbe(): React.JSX.Element {
  const context = useWorkspace();
  return <span data-testid="ws-probe-status">{context.status}</span>;
}

function renderSidebarInWorkspace(opts: {
  myRole: WorkspaceRole;
  collapsed?: boolean;
}): ReturnType<typeof renderWithProviders> {
  const client = stubWorkspaceClient(opts.myRole);
  return renderWithProviders(
    <WorkspaceProvider slug="acme" client={client as never}>
      <Sidebar collapsed={opts.collapsed ?? false} onToggleCollapsed={() => undefined} />
      <WorkspaceStatusProbe />
    </WorkspaceProvider>,
  );
}

describe('Sidebar 工作区设置入口(§6.12 角色可见性)', () => {
  it('admin 工作区就绪后展开态呈现设置入口,指向当前工作区设置页', async () => {
    renderSidebarInWorkspace({ myRole: 'owner' });
    const link = await screen.findByTestId('nav-workspace-settings');
    expect(link.getAttribute('href')).toBe('/w/acme/settings');
    expect(link.textContent).toBe('Workspace settings');
    // 统一 SVG 图标(§7.1);展开态不经 Tooltip 包裹
    expect(link.querySelector('svg')).not.toBeNull();
    expect(link.getAttribute('aria-describedby')).toBeNull();
  });

  it('折叠态设置入口经 Tooltip 包裹补齐可读名,链接与 testid 保留(§7.1)', async () => {
    renderSidebarInWorkspace({ myRole: 'admin', collapsed: true });
    const link = await screen.findByTestId('nav-workspace-settings');
    expect(link.getAttribute('href')).toBe('/w/acme/settings');
    // 折叠态经 Tooltip 包裹:aria-describedby 关联到 role=tooltip 可读名
    const describedBy = link.getAttribute('aria-describedby');
    expect(describedBy).not.toBeNull();
    const tooltip = describedBy !== null ? document.getElementById(describedBy) : null;
    expect(tooltip?.textContent).toBe('Workspace settings');
  });

  it('member 角色不呈现工作区设置入口', async () => {
    renderSidebarInWorkspace({ myRole: 'member' });
    await waitFor(() => expect(screen.getByTestId('ws-probe-status').textContent).toBe('ready'));
    expect(screen.queryByTestId('nav-workspace-settings')).toBeNull();
  });

  it('无工作区上下文(顶层路由)不呈现设置入口', () => {
    renderSidebar();
    expect(screen.queryByTestId('nav-workspace-settings')).toBeNull();
  });
});
