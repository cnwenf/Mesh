/**
 * App 路由与 Provider 组装 — '/'首页、'/settings'设置、'*'→404、'/login'登录;
 * TopBar 命令面板/帮助入口开启对应对话框。无 token,故实时不建连(不触 WS);
 * 首页实时演示的 GET 以 mock fetch 提供(不触真实网络)。
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../../App';
import { useAuthStore } from '../../state/authStore';
import { useSettingsStore } from '../../state/settingsStore';
import { useShortcutRegistry } from '../../shortcuts';

function navigateTo(path: string): void {
  window.history.replaceState({}, '', path);
}

describe('App 路由', () => {
  beforeEach(() => {
    useAuthStore.getState().clearToken();
    useSettingsStore.getState().resetPreferences();
    useShortcutRegistry.setState({ commands: [], shortcuts: [], activeContexts: [] });
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ data: [], next_cursor: null }), { status: 200 })),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    navigateTo('/');
  });

  it('/ 渲染首页(shell + 导航 + home 标题 + 实时演示区)', async () => {
    navigateTo('/');
    render(<App />);
    expect(screen.getByText('Welcome to Mesh')).toBeInTheDocument();
    expect(screen.getByTestId('nav-home')).toBeInTheDocument();
    expect(screen.getByTestId('topbar-search')).toBeInTheDocument();
    // AppShell 内实时上下文非空:实时演示区渲染(创建表单 + 列表)
    await waitFor(() => expect(screen.getByTestId('demo-issue-list')).toBeInTheDocument());
    expect(screen.getByTestId('demo-create')).toBeInTheDocument();
  });

  it('/settings 渲染设置页(主题/语言/时区选择器)', () => {
    navigateTo('/settings');
    render(<App />);
    expect(screen.getByTestId('theme-select')).toBeInTheDocument();
    expect(screen.getByTestId('locale-select')).toBeInTheDocument();
    expect(screen.getByTestId('timezone-select')).toBeInTheDocument();
  });

  it('/inbox 渲染占位页', () => {
    navigateTo('/inbox');
    render(<App />);
    // 占位空态描述(侧栏 nav 项不含此文案,避免多匹配)
    expect(screen.getByText('Items you create or follow will show up here.')).toBeInTheDocument();
  });

  it('未知路由渲染 404', () => {
    navigateTo('/definitely-missing');
    render(<App />);
    expect(screen.getByText('Page not found')).toBeInTheDocument();
  });

  it('/login 未登录时渲染登录页', () => {
    navigateTo('/login');
    render(<App />);
    expect(screen.getByTestId('login-token')).toBeInTheDocument();
  });

  it('TopBar 命令面板/帮助按钮开启对应对话框', () => {
    navigateTo('/');
    render(<App />);
    fireEvent.click(screen.getByTestId('open-palette'));
    expect(screen.getByText('Command palette')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('open-help'));
    expect(screen.getByText('Keyboard shortcuts')).toBeInTheDocument();
  });
});
