/**
 * MobileMoreDrawer — 「更多」导航抽屉契约(design-quality §4.3)。
 */
import { useState } from 'react';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../test-utils/render';
import { WorkspaceProvider, useWorkspace } from '../../workspace/WorkspaceProvider';
import type { WorkspaceRole } from '../../api/workspace';
import { MobileMoreDrawer } from '../MobileMoreDrawer';

function renderDrawer(open = true, onClose = vi.fn()): ReturnType<typeof renderWithProviders> {
  return renderWithProviders(<MobileMoreDrawer open={open} onClose={onClose} />);
}

describe('MobileMoreDrawer(「更多」导航抽屉)', () => {
  it('open=false 时不渲染', () => {
    renderDrawer(false);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('打开时以 dialog 呈现全部次级导航入口', () => {
    renderDrawer();
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    for (const key of [
      'inbox',
      'projects',
      'cycles',
      'members',
      'skills',
      'squads',
      'approvals',
      'autopilots',
      'runtimes',
      'insights',
      'integrations',
      'settings',
    ]) {
      const link = screen.getByTestId('mobile-drawer-nav-' + key);
      expect(link).toBeInTheDocument();
      // 每项携带统一 SVG 图标(§7.1)
      expect(link.querySelector('svg')).not.toBeNull();
    }
  });

  it('入口按 §4.1 分组呈现(组标题可见)', () => {
    renderDrawer();
    const titles = screen
      .getAllByRole('heading', { level: 2 })
      .map((heading) => heading.textContent);
    // 抽屉自身标题之后依次为四分组标题
    expect(titles.slice(-4)).toEqual(['Work', 'Team', 'Run', 'Admin']);
  });

  it('Esc 触发 onClose', () => {
    const onClose = vi.fn();
    renderDrawer(true, onClose);
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('关闭按钮触发 onClose(鼠标/触控等价路径)', () => {
    const onClose = vi.fn();
    renderDrawer(true, onClose);
    fireEvent.click(screen.getByRole('button', { name: 'Close navigation menu' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('点击导航项后关闭抽屉(进入目标页由路由负责)', () => {
    const onClose = vi.fn();
    renderDrawer(true, onClose);
    fireEvent.click(screen.getByTestId('mobile-drawer-nav-members'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('Tab 在抽屉内循环(末项 Tab → 首项;首项 Shift+Tab → 末项)', async () => {
    renderDrawer();
    const dialog = screen.getByRole('dialog');
    const close = screen.getByRole('button', { name: 'Close navigation menu' });
    const lastLink = screen.getByTestId('mobile-drawer-nav-settings');
    // 末项 Tab → 回首项(关闭按钮为面板首个可聚焦元素)
    lastLink.focus();
    fireEvent.keyDown(dialog, { key: 'Tab' });
    expect(close).toHaveFocus();
    // 首项 Shift+Tab → 回末项
    close.focus();
    fireEvent.keyDown(dialog, { key: 'Tab', shiftKey: true });
    expect(lastLink).toHaveFocus();
    // 面板自身聚焦时 Shift+Tab 同样跳末项;非 Tab 键不干预
    dialog.focus();
    fireEvent.keyDown(dialog, { key: 'Tab', shiftKey: true });
    expect(lastLink).toHaveFocus();
    fireEvent.keyDown(dialog, { key: 'a' });
    expect(lastLink).toHaveFocus();
  });

  it('打开后焦点进入抽屉;关闭后归还触发元素', async () => {
    const user = userEvent.setup();
    function Harness(): React.JSX.Element {
      const [open, setOpen] = useState(false);
      return (
        <div>
          <button type="button" onClick={() => setOpen(true)}>
            Open drawer
          </button>
          <MobileMoreDrawer open={open} onClose={() => setOpen(false)} />
        </div>
      );
    }
    renderWithProviders(<Harness />);
    const trigger = screen.getByRole('button', { name: 'Open drawer' });
    await user.click(trigger);
    await waitFor(() => expect(screen.getByRole('dialog')).toHaveFocus());
    await user.keyboard('{Escape}');
    await waitFor(() => expect(trigger).toHaveFocus());
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

function renderDrawerInWorkspace(opts: {
  myRole: WorkspaceRole;
  onClose?: () => void;
}): ReturnType<typeof renderWithProviders> {
  const client = stubWorkspaceClient(opts.myRole);
  return renderWithProviders(
    <WorkspaceProvider slug="acme" client={client as never}>
      <MobileMoreDrawer open onClose={opts.onClose ?? vi.fn()} />
      <WorkspaceStatusProbe />
    </WorkspaceProvider>,
  );
}

describe('MobileMoreDrawer 工作区设置入口(§6.12 角色可见性)', () => {
  it('工作区上下文内逐项提供并执行全部 More 规范链接,账号设置保持全局', async () => {
    const onClose = vi.fn();
    renderDrawerInWorkspace({ myRole: 'owner', onClose });
    await screen.findByTestId('mobile-drawer-nav-workspace-settings');
    const expected: Readonly<Record<string, string>> = {
      inbox: '/w/acme/inbox',
      projects: '/w/acme/projects',
      cycles: '/w/acme/cycles',
      members: '/w/acme/members',
      skills: '/w/acme/automations/skills',
      squads: '/w/acme/squads',
      approvals: '/w/acme/approvals',
      autopilots: '/w/acme/automations/autopilots',
      runtimes: '/w/acme/automations/runtimes',
      insights: '/w/acme/insights',
      integrations: '/w/acme/automations/integrations',
      settings: '/settings',
    };
    for (const [key, href] of Object.entries(expected)) {
      const link = screen.getByTestId(`mobile-drawer-nav-${key}`);
      expect(link).toHaveAttribute('href', href);
      fireEvent.click(link);
    }
    expect(onClose).toHaveBeenCalledTimes(Object.keys(expected).length);
  });

  it('admin 工作区就绪后抽屉呈现设置入口,点击后关闭抽屉', async () => {
    const onClose = vi.fn();
    renderDrawerInWorkspace({ myRole: 'owner', onClose });
    const link = await screen.findByTestId('mobile-drawer-nav-workspace-settings');
    expect(link.getAttribute('href')).toBe('/w/acme/settings');
    expect(screen.getByTestId('mobile-drawer-nav-members')).toHaveAttribute(
      'href',
      '/w/acme/members',
    );
    expect(link.textContent).toBe('Workspace settings');
    expect(link.querySelector('svg')).not.toBeNull();
    fireEvent.click(link);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('member 角色抽屉不呈现工作区设置入口', async () => {
    renderDrawerInWorkspace({ myRole: 'member' });
    await waitFor(() => expect(screen.getByTestId('ws-probe-status').textContent).toBe('ready'));
    expect(screen.queryByTestId('mobile-drawer-nav-workspace-settings')).toBeNull();
  });

  it('无工作区上下文不呈现设置入口', () => {
    renderDrawer();
    expect(screen.queryByTestId('mobile-drawer-nav-workspace-settings')).toBeNull();
  });
});
