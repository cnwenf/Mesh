/**
 * SettingsLayout — 二级导航分组渲染、当前路由高亮(MemoryRouter)、hidden 项跳过、
 * 全隐藏组不渲染、标题/描述、导航区可访问名(aria-label)。
 * 桌面左栏 / 手机顶部分组列表为纯 CSS(≤599px),此处断言结构与 ARIA。
 */
import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderWithProviders } from '../../test-utils/render';
import { SettingsLayout } from '../patterns/SettingsLayout';
import type { SettingsNavGroup } from '../patterns/SettingsLayout';

const GROUPS: ReadonlyArray<SettingsNavGroup> = [
  {
    label: 'Account',
    items: [
      { key: 'appearance', label: 'Appearance', to: '/settings/appearance', icon: 'settings' },
      { key: 'notifications', label: 'Notifications', to: '/settings/notifications', icon: 'bell' },
    ],
  },
  {
    label: 'Access',
    items: [{ key: 'security', label: 'Security', to: '/settings/security', icon: 'user' }],
  },
];

function renderAt(route: string, groups: ReadonlyArray<SettingsNavGroup> = GROUPS): void {
  renderWithProviders(
    <SettingsLayout title="Settings" description="Manage your account" groups={groups} navLabel="Settings navigation">
      <div data-testid="content">content</div>
    </SettingsLayout>,
    { route },
  );
}

describe('SettingsLayout', () => {
  it('渲染标题、描述与内容列', () => {
    renderAt('/settings/appearance');
    expect(screen.getByRole('heading', { level: 1, name: 'Settings' })).toBeInTheDocument();
    expect(screen.getByText('Manage your account')).toBeInTheDocument();
    expect(screen.getByTestId('content')).toBeInTheDocument();
  });

  it('导航区带可访问名并按组渲染组标题与项', () => {
    renderAt('/settings/appearance');
    const nav = screen.getByRole('navigation', { name: 'Settings navigation' });
    expect(nav).toBeInTheDocument();
    expect(screen.getByText('Account')).toBeInTheDocument();
    expect(screen.getByText('Access')).toBeInTheDocument();
    expect(screen.getByTestId('settings-nav-appearance')).toBeInTheDocument();
    expect(screen.getByTestId('settings-nav-notifications')).toBeInTheDocument();
    expect(screen.getByTestId('settings-nav-security')).toBeInTheDocument();
  });

  it('当前路由项高亮(is-active + aria-current),其余项不高亮', () => {
    renderAt('/settings/notifications');
    const active = screen.getByTestId('settings-nav-notifications');
    expect(active.className).toContain('is-active');
    expect(active).toHaveAttribute('aria-current', 'page');
    const other = screen.getByTestId('settings-nav-appearance');
    expect(other.className).not.toContain('is-active');
    expect(other).not.toHaveAttribute('aria-current');
  });

  it('hidden 项不渲染(权限不可见)', () => {
    const groups: ReadonlyArray<SettingsNavGroup> = [
      {
        items: [
          { key: 'visible', label: 'Visible', to: '/a' },
          { key: 'secret', label: 'Secret', to: '/b', hidden: true },
        ],
      },
    ];
    renderAt('/a', groups);
    expect(screen.getByTestId('settings-nav-visible')).toBeInTheDocument();
    expect(screen.queryByTestId('settings-nav-secret')).not.toBeInTheDocument();
  });

  it('全部项隐藏的组不渲染组标题', () => {
    const groups: ReadonlyArray<SettingsNavGroup> = [
      { label: 'HiddenGroup', items: [{ key: 'x', label: 'X', to: '/x', hidden: true }] },
      { label: 'ShownGroup', items: [{ key: 'y', label: 'Y', to: '/y' }] },
    ];
    renderAt('/y', groups);
    expect(screen.queryByText('HiddenGroup')).not.toBeInTheDocument();
    expect(screen.getByText('ShownGroup')).toBeInTheDocument();
  });

  it('无 label 的组不渲染组标题但仍渲染项', () => {
    const groups: ReadonlyArray<SettingsNavGroup> = [
      { items: [{ key: 'solo', label: 'Solo', to: '/solo' }] },
    ];
    renderAt('/solo', groups);
    expect(screen.getByTestId('settings-nav-solo')).toBeInTheDocument();
  });

  it('导航项以链接呈现(可键盘聚焦),移动端分组列表为纯 CSS', () => {
    renderAt('/settings/appearance');
    const link = screen.getByTestId('settings-nav-appearance');
    expect(link.tagName).toBe('A');
    expect(link).toHaveAttribute('href', '/settings/appearance');
    // 结构:nav > group > ul > li > a(手机/桌面同构,布局靠 CSS)
    const nav = screen.getByRole('navigation', { name: 'Settings navigation' });
    expect(nav.querySelectorAll('ul').length).toBeGreaterThan(0);
  });
});
