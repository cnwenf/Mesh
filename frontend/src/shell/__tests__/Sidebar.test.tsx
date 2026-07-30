/**
 * Sidebar — 分组侧栏(design-quality §4.1):四分组 + 折叠 rail + 图标 + 激活态。
 */
import { useState } from 'react';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '../../test-utils/render';
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
  { testid: 'nav-autopilots', label: 'Autopilots', href: '/autopilots' },
  // 运行环境独立入口(§4.1:中文与自动值守区分,不再同名「自动化」)
  { testid: 'nav-runtimes', label: 'Runtimes', href: '/runtimes' },
  { testid: 'nav-insights', label: 'Insights', href: '/insights' },
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
