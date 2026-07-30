/**
 * App 路由与 Provider 组装 — '/'首页、'/settings'设置、'*'→404、'/login'登录;
 * TopBar 命令面板/帮助入口开启对应对话框。
 *
 * MES-106:受保护路由位于 RequireAuth 守卫之后——未登录访问统一跳
 * /login?next=<原路径>;受保护页用例先写入 token(登录态)再渲染。
 * 实时 WebSocket 以空操作替身桩平(不建真实连接);首页实时演示的 GET
 * 以 mock fetch 提供(不触真实网络)。
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

/** 空操作 WebSocket 替身:捕获 URL,不建真实连接、不触发回调。 */
class FakeWebSocket {
  static urls: string[] = [];

  onopen: (() => void) | null = null;

  onmessage: ((ev: { data: string }) => void) | null = null;

  onclose: (() => void) | null = null;

  onerror: (() => void) | null = null;

  readyState = 0;

  constructor(url: string) {
    FakeWebSocket.urls.push(url);
  }

  send(): void {}

  close(): void {}
}

/** 写入登录态(受保护路由用例前置) */
function signIn(): void {
  useAuthStore.getState().setToken('tok_test');
}

describe('App 路由', () => {
  beforeEach(() => {
    useAuthStore.getState().clearToken();
    useSettingsStore.getState().resetPreferences();
    useShortcutRegistry.setState({ commands: [], shortcuts: [], activeContexts: [] });
    vi.stubGlobal('WebSocket', FakeWebSocket);
    // /users/me 返回一个合法成员身份(供收件箱/通知偏好解析 workspace);其余默认空列表包络。
    const me = {
      user: { id: 'usr-1', email: 'o@c.com', display_name: 'Owner' },
      memberships: [
        { workspace_id: 'ws-1', workspace_name: 'WS', workspace_slug: 'ws', role: 'owner', status: 'active', joined_at: null },
      ],
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const body = url.includes('/users/me') ? { data: me } : { data: [], next_cursor: null };
        return new Response(JSON.stringify(body), { status: 200 });
      }),
    );
  });
  afterEach(() => {
    useAuthStore.getState().clearToken();
    vi.unstubAllGlobals();
    FakeWebSocket.urls = [];
    navigateTo('/');
  });

  it('/ 渲染真实首页(shell + 导航 + 问候 + 工作区卡片 + 仪表盘, MES-107)', async () => {
    signIn();
    navigateTo('/');
    render(<App />);
    await waitFor(() => expect(screen.getByTestId('home-greeting')).toBeInTheDocument());
    expect(screen.getByTestId('home-greeting').textContent).toContain('Owner');
    expect(screen.getByTestId('nav-home')).toBeInTheDocument();
    expect(screen.getByTestId('topbar-search')).toBeInTheDocument();
    expect(screen.getByTestId('home-workspace-ws')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('home-dashboard')).toBeInTheDocument());
    expect(screen.getByTestId('home-create')).toBeInTheDocument();
  });

  it('/settings 渲染设置页(主题/语言/时区选择器)', () => {
    signIn();
    navigateTo('/settings');
    render(<App />);
    expect(screen.getByTestId('theme-select')).toBeInTheDocument();
    expect(screen.getByTestId('locale-select')).toBeInTheDocument();
    expect(screen.getByTestId('timezone-select')).toBeInTheDocument();
  });

  it('/inbox 渲染收件箱页(空态)', async () => {
    signIn();
    navigateTo('/inbox');
    render(<App />);
    // 收件箱页解析到工作区后拉取通知;mock 返回空列表 → 呈现 onboarding 四要素空态文案。
    await waitFor(() => expect(screen.getByText('No notifications yet')).toBeInTheDocument());
  });

  it('未知路由渲染 404', () => {
    navigateTo('/definitely-missing');
    render(<App />);
    expect(screen.getByText('Page not found')).toBeInTheDocument();
  });

  it('/login 未登录时渲染登录页', () => {
    navigateTo('/login');
    render(<App />);
    expect(screen.getByTestId('login-email')).toBeInTheDocument();
  });

  it('TopBar 命令面板/帮助按钮开启对应对话框', () => {
    signIn();
    navigateTo('/');
    render(<App />);
    fireEvent.click(screen.getByTestId('open-palette'));
    expect(screen.getByText('Command palette')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('open-help'));
    expect(screen.getByText('Keyboard shortcuts')).toBeInTheDocument();
  });

  it('URL ?locale= 显式请求参数为最高优先(§6.18 请求显式参数级)', async () => {
    signIn();
    navigateTo('/?locale=zh-CN');
    render(<App />);
    expect(await screen.findByText('欢迎回来,Owner')).toBeInTheDocument();
  });

  it('navigator.languages 不可用时系统级候选为空,回退 en', async () => {
    signIn();
    const spy = vi.spyOn(navigator, 'languages', 'get').mockReturnValue(undefined as never);
    navigateTo('/');
    render(<App />);
    expect(await screen.findByText('Welcome back, Owner')).toBeInTheDocument();
    spy.mockRestore();
  });
});

describe('App 登录守卫(MES-106:未登录访问受保护页 → /login?next=)', () => {
  beforeEach(() => {
    useAuthStore.getState().clearToken();
    useSettingsStore.getState().resetPreferences();
    useShortcutRegistry.setState({ commands: [], shortcuts: [], activeContexts: [] });
    vi.stubGlobal('WebSocket', FakeWebSocket);
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        // 邀请预览公开端点:返回不可用预览(四 reason 态之一),验证公开路由可达。
        const body = url.includes('/invitations/preview')
          ? { data: { valid: false, reason: 'expired' } }
          : { data: [], next_cursor: null };
        return new Response(JSON.stringify(body), { status: 200 });
      }),
    );
  });
  afterEach(() => {
    useAuthStore.getState().clearToken();
    vi.unstubAllGlobals();
    FakeWebSocket.urls = [];
    navigateTo('/');
  });

  it('未登录访问首页 → 渲染登录页,URL 携带 next=/(编码)', () => {
    navigateTo('/');
    render(<App />);
    expect(screen.getByTestId('login-email')).toBeInTheDocument();
    expect(window.location.pathname).toBe('/login');
    expect(window.location.search).toContain(`next=${encodeURIComponent('/')}`);
  });

  it('未登录访问深层受保护路径 → next 携带原路径与查询串', () => {
    navigateTo('/issues?focus=1');
    render(<App />);
    expect(screen.getByTestId('login-email')).toBeInTheDocument();
    expect(window.location.pathname).toBe('/login');
    expect(window.location.search).toContain(`next=${encodeURIComponent('/issues?focus=1')}`);
  });

  it('未登录访问邀请接受页(公开)→ 不被守卫拦截', async () => {
    navigateTo('/invite/invtk_x');
    render(<App />);
    // 邀请预览公开可见:mock 预览不可用(expired)→ reason 态呈现(公开路由可达,守卫未拦)
    await waitFor(() => expect(screen.getByTestId('invite-reason-expired')).toBeInTheDocument());
    expect(screen.queryByTestId('login-email')).not.toBeInTheDocument();
  });

  it('未登录时受保护页不发起实时连接(不构造 WebSocket)', () => {
    navigateTo('/');
    render(<App />);
    expect(FakeWebSocket.urls).toEqual([]);
  });
});
