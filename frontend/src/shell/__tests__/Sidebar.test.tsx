/**
 * Sidebar — 目录项经消息目录本地化,testid 为 nav-<key>,首页精确激活。
 */
import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderWithProviders } from '../../test-utils/render';
import { Sidebar } from '../Sidebar';

const EXPECTED: ReadonlyArray<{ testid: string; label: string; href: string }> = [
  { testid: 'nav-home', label: 'Home', href: '/' },
  { testid: 'nav-inbox', label: 'Inbox', href: '/inbox' },
  { testid: 'nav-projects', label: 'Projects', href: '/projects' },
  { testid: 'nav-board', label: 'Board', href: '/board' },
  { testid: 'nav-members', label: 'Members', href: '/members' },
  { testid: 'nav-chat', label: 'Chat', href: '/chat' },
  { testid: 'nav-automation', label: 'Automation', href: '/automation' },
  { testid: 'nav-settings', label: 'Settings', href: '/settings' },
];

describe('Sidebar', () => {
  it('渲染全部导航项(目录文案 + testid + href)', () => {
    renderWithProviders(<Sidebar />);
    for (const { testid, label, href } of EXPECTED) {
      const link = screen.getByTestId(testid);
      expect(link.textContent).toBe(label);
      expect(link.getAttribute('href')).toBe(href);
    }
  });

  it('当前路由的导航项带激活样式,首页为精确匹配', () => {
    renderWithProviders(<Sidebar />, { route: '/board' });
    expect(screen.getByTestId('nav-board').className).toContain('mesh-sidebar__link--active');
    expect(screen.getByTestId('nav-home').className).not.toContain('mesh-sidebar__link--active');
  });

  it('选中视图路由 /views/{id} 下看板入口保持激活(§4.2 URL 同步)', () => {
    renderWithProviders(<Sidebar />, { route: '/views/v1' });
    expect(screen.getByTestId('nav-board').className).toContain('mesh-sidebar__link--active');
    expect(screen.getByTestId('nav-home').className).not.toContain('mesh-sidebar__link--active');
  });

  it('根路由下仅首页激活(精确匹配,不吞并子路由)', () => {
    renderWithProviders(<Sidebar />, { route: '/' });
    expect(screen.getByTestId('nav-home').className).toContain('mesh-sidebar__link--active');
    expect(screen.getByTestId('nav-inbox').className).not.toContain('mesh-sidebar__link--active');
  });
});
