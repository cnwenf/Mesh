import { act, render } from '@testing-library/react';
import { useTheme as useAppicaTheme } from '@appica/ui-react/hooks/use-theme';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { defaultPreferences, useSettingsStore } from '../../state/settingsStore';
import { useWorkspaceThemeBridge } from '../../state/workspaceThemeBridge';
import { THEME_LOCATOR_KEY } from '../themeLocator';
import { ThemeProvider, resolveTheme } from '../ThemeProvider';
import { DARK_TOKENS, LIGHT_TOKENS } from '../tokenValues';

type ChangeListener = (event: { matches: boolean }) => void;

interface MatchMediaControl {
  setMatches: (next: boolean) => void;
  addEventListener: ReturnType<typeof vi.fn>;
  removeEventListener: ReturnType<typeof vi.fn>;
}

const originalMatchMedia = window.matchMedia;

function stubMatchMedia(initialMatches: boolean): MatchMediaControl {
  const listeners = new Set<ChangeListener>();
  const state = { matches: initialMatches };
  const addEventListener = vi.fn((_event: string, listener: ChangeListener) => {
    listeners.add(listener);
  });
  const removeEventListener = vi.fn((_event: string, listener: ChangeListener) => {
    listeners.delete(listener);
  });
  window.matchMedia = vi.fn().mockImplementation(
    (query: string) =>
      ({
        get matches(): boolean {
          return state.matches;
        },
        media: query,
        onchange: null,
        addListener: () => undefined,
        removeListener: () => undefined,
        addEventListener,
        removeEventListener,
        dispatchEvent: () => true,
      }) as unknown as MediaQueryList,
  );
  return {
    setMatches: (next: boolean) => {
      state.matches = next;
      for (const listener of [...listeners]) listener({ matches: next });
    },
    addEventListener,
    removeEventListener,
  };
}

beforeEach(() => {
  localStorage.clear();
  useSettingsStore.setState({ preferences: defaultPreferences(), lastSyncError: null });
  useWorkspaceThemeBridge.setState({ defaultTheme: null, loaded: true });
  delete document.documentElement.dataset.theme;
  document.documentElement.removeAttribute('data-theme-pending');
  document.documentElement.classList.remove('light', 'dark');
  stubMatchMedia(false);
});

afterEach(() => {
  window.matchMedia = originalMatchMedia;
  vi.restoreAllMocks();
});

describe('resolveTheme(纯函数兼容导出)', () => {
  it('light/dark 模式直接返回,不咨询系统', () => {
    expect(resolveTheme('light', true)).toBe('light');
    expect(resolveTheme('light', false)).toBe('light');
    expect(resolveTheme('dark', true)).toBe('dark');
    expect(resolveTheme('dark', false)).toBe('dark');
  });

  it('system 模式跟随系统偏好', () => {
    expect(resolveTheme('system', true)).toBe('dark');
    expect(resolveTheme('system', false)).toBe('light');
  });
});

describe('ThemeProvider 协商链(theme.md §2.2)', () => {
  function AppicaThemeProbe(): React.JSX.Element {
    const theme = useAppicaTheme();
    return (
      <output data-testid="appica-theme">
        {theme.forcedTheme ?? 'none'}:{theme.resolvedTheme ?? 'none'}
      </output>
    );
  }

  it('向 Appica provider 下发同一权威主题,不创建第二条协商链', () => {
    useWorkspaceThemeBridge.setState({ defaultTheme: 'dark', loaded: true });
    const { getByTestId } = render(
      <ThemeProvider>
        <AppicaThemeProbe />
      </ThemeProvider>,
    );
    expect(getByTestId('appica-theme')).toHaveTextContent('dark:dark');
    expect(document.documentElement).toHaveClass('dark');
    expect(document.documentElement).not.toHaveClass('light');
  });

  it('user=null + 工作区默认 dark → 应用暗色(第 2 级)', () => {
    useWorkspaceThemeBridge.setState({ defaultTheme: 'dark', loaded: true });
    render(
      <ThemeProvider>
        <p>content</p>
      </ThemeProvider>,
    );
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  it('user=system + 工作区默认 dark + OS 浅色 → 浅色(显式 system 忽略工作区)', () => {
    stubMatchMedia(false);
    useWorkspaceThemeBridge.setState({ defaultTheme: 'dark', loaded: true });
    act(() => useSettingsStore.getState().setTheme('system'));
    render(
      <ThemeProvider>
        <p>content</p>
      </ThemeProvider>,
    );
    expect(document.documentElement.dataset.theme).toBe('light');
  });

  it('user=dark 终止于用户级(忽略工作区默认 light)', () => {
    useWorkspaceThemeBridge.setState({ defaultTheme: 'light', loaded: true });
    act(() => useSettingsStore.getState().setTheme('dark'));
    render(
      <ThemeProvider>
        <p>content</p>
      </ThemeProvider>,
    );
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  it('user=null + 工作区默认 system + OS 暗 → 暗(落系统级)', () => {
    stubMatchMedia(true);
    useWorkspaceThemeBridge.setState({ defaultTheme: 'system', loaded: true });
    render(
      <ThemeProvider>
        <p>content</p>
      </ThemeProvider>,
    );
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  it('无工作区上下文(loaded=true, null)+ OS 浅 → 浅色', () => {
    render(
      <ThemeProvider>
        <p>content</p>
      </ThemeProvider>,
    );
    expect(document.documentElement.dataset.theme).toBe('light');
  });

  it('system 模式实时响应 prefers-color-scheme 变化(T8)', () => {
    const control = stubMatchMedia(false);
    act(() => useSettingsStore.getState().setTheme('system'));
    render(
      <ThemeProvider>
        <p>content</p>
      </ThemeProvider>,
    );
    expect(document.documentElement.dataset.theme).toBe('light');
    act(() => control.setMatches(true));
    expect(document.documentElement.dataset.theme).toBe('dark');
    act(() => control.setMatches(false));
    expect(document.documentElement.dataset.theme).toBe('light');
  });

  it('显式 light/dark 模式下系统偏好变化不影响结果', () => {
    const control = stubMatchMedia(false);
    act(() => useSettingsStore.getState().setTheme('light'));
    render(
      <ThemeProvider>
        <p>content</p>
      </ThemeProvider>,
    );
    act(() => control.setMatches(true));
    expect(document.documentElement.dataset.theme).toBe('light');
  });

  it('偏好变更即时生效(无刷新,仅改 data-theme)', () => {
    render(
      <ThemeProvider>
        <p>content</p>
      </ThemeProvider>,
    );
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(document.documentElement).toHaveClass('light');
    expect(document.documentElement).not.toHaveClass('dark');
    act(() => useSettingsStore.getState().setTheme('dark'));
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(document.documentElement).toHaveClass('dark');
    expect(document.documentElement).not.toHaveClass('light');
    act(() => useSettingsStore.getState().setTheme(null));
    expect(document.documentElement.dataset.theme).toBe('light');
  });

  it('卸载时注销 matchMedia 监听器', () => {
    const control = stubMatchMedia(false);
    act(() => useSettingsStore.getState().setTheme('light'));
    const { unmount } = render(
      <ThemeProvider>
        <p>content</p>
      </ThemeProvider>,
    );
    expect(control.addEventListener).toHaveBeenCalledWith('change', expect.any(Function));
    unmount();
    expect(control.removeEventListener).toHaveBeenCalledWith('change', expect.any(Function));
  });
});

describe('ThemeProvider skeleton 兜底(theme.md §2.3 ③)', () => {
  // skeleton 仅在「路由期望工作区默认 且 桥接未就绪 且 已探测会话」时出现(H3):
  // 用工作区路由触发 routeExpectsWorkspaceDefault=true,并置 sessionProbed=true
  // (模拟已登录 bootstrap 完成),否则全局路由/匿名协商链可直接落系统级或不陷 skeleton。
  beforeEach(() => {
    window.history.pushState({}, '', '/w/ws-skel/board');
    useSettingsStore.setState({ sessionProbed: true });
  });
  afterEach(() => {
    window.history.pushState({}, '', '/');
  });

  it('user=null + 工作区默认未就绪 → skeleton 覆盖视口,children 隐藏但保持挂载', () => {
    useWorkspaceThemeBridge.setState({ defaultTheme: null, loaded: false });
    const { getByTestId, getByText } = render(
      <ThemeProvider>
        <p>business content</p>
      </ThemeProvider>,
    );
    expect(getByTestId('theme-skeleton')).toBeTruthy();
    // 业务内容隐藏(不呈现),但保持挂载(其中含供给协商链第 2 级的组件)。
    const wrapper = getByTestId('theme-children-root');
    expect(wrapper.style.display).toBe('none');
    expect(getByText('business content')).toBeTruthy();
  });

  it('工作区默认就绪后 skeleton 消失、children 呈现并落主题', () => {
    useWorkspaceThemeBridge.setState({ defaultTheme: null, loaded: false });
    const { getByTestId, queryByTestId } = render(
      <ThemeProvider>
        <p>business content</p>
      </ThemeProvider>,
    );
    expect(queryByTestId('theme-skeleton')).not.toBeNull();
    act(() => useWorkspaceThemeBridge.getState().setWorkspaceDefault('dark'));
    expect(queryByTestId('theme-skeleton')).toBeNull();
    expect(getByTestId('theme-children-root').style.display).toBe('contents');
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  it('显式用户偏好不受工作区未就绪影响(不 skeleton)', () => {
    useWorkspaceThemeBridge.setState({ defaultTheme: null, loaded: false });
    act(() => useSettingsStore.getState().setTheme('dark'));
    const { getByText, queryByTestId } = render(
      <ThemeProvider>
        <p>business content</p>
      </ThemeProvider>,
    );
    expect(getByText('business content')).toBeTruthy();
    expect(queryByTestId('theme-skeleton')).toBeNull();
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  it('H3: 工作区路由 + 注入首帧 + 桥接未就绪 → 保持注入值,不覆盖、不写错 locator', () => {
    // 服务端按当前路由协商注入 dark;桥接尚未就绪时链不可信,Provider 必须
    // 保持首帧(data-theme=dark),不得用 chain(null,null,OS=light) 覆盖。
    document.documentElement.dataset.theme = 'dark';
    useWorkspaceThemeBridge.setState({ defaultTheme: null, loaded: false });
    render(
      <ThemeProvider>
        <p>content</p>
      </ThemeProvider>,
    );
    expect(document.documentElement.dataset.theme).toBe('dark');
    // 保持期间不写 locator(避免以不可信链值污染分区镜像)。
    expect(localStorage.getItem('mesh.theme.active')).toBeNull();
    // 桥接就绪后链可信,应用并回写 locator(与注入一致 → 无翻转)。
    act(() => useWorkspaceThemeBridge.getState().setWorkspaceDefault('dark'));
    expect(document.documentElement.dataset.theme).toBe('dark');
    const locator = JSON.parse(localStorage.getItem('mesh.theme.active') ?? 'null') as {
      mode: string;
    } | null;
    expect(locator?.mode).toBe('dark');
  });

  it('首帧已注入 data-theme(无 pending 标记)→ 不闪 skeleton', () => {
    document.documentElement.dataset.theme = 'dark';
    useWorkspaceThemeBridge.setState({ defaultTheme: null, loaded: false });
    const { queryByTestId } = render(
      <ThemeProvider>
        <p>business content</p>
      </ThemeProvider>,
    );
    expect(queryByTestId('theme-skeleton')).toBeNull();
  });

  it('解析完成移除 data-theme-pending 标记', () => {
    document.documentElement.setAttribute('data-theme-pending', '');
    act(() => useSettingsStore.getState().setTheme('light'));
    render(
      <ThemeProvider>
        <p>content</p>
      </ThemeProvider>,
    );
    expect(document.documentElement.hasAttribute('data-theme-pending')).toBe(false);
  });
});

describe('ThemeProvider locator 回写(theme.md §2.3 ②)', () => {
  it('每次解析完成以当前路由身份回写 mesh.theme.active', () => {
    act(() => useSettingsStore.getState().setTheme('dark'));
    render(
      <ThemeProvider>
        <p>content</p>
      </ThemeProvider>,
    );
    const raw = localStorage.getItem(THEME_LOCATOR_KEY);
    expect(raw).not.toBeNull();
    const locator = JSON.parse(raw as string) as { id: string; mode: string };
    expect(locator.mode).toBe('dark');
    expect(locator.id).toMatch(/:app$|:w:|:invite$|:anon$/);
  });
});

describe('ThemeProvider meta theme-color 联动(theme.md §4.2)', () => {
  function seedMetas(): void {
    document.head.innerHTML =
      '<meta name="theme-color" media="(prefers-color-scheme: light)" content="#f9fafb">' +
      '<meta name="theme-color" media="(prefers-color-scheme: dark)" content="#1e293b">';
  }

  it('显式切换 dark → 两条 meta 均改写为暗色表面色', () => {
    seedMetas();
    act(() => useSettingsStore.getState().setTheme('dark'));
    render(
      <ThemeProvider>
        <p>content</p>
      </ThemeProvider>,
    );
    const metas = document.querySelectorAll('meta[name="theme-color"]');
    expect(metas[0].getAttribute('content')).toBe(DARK_TOKENS['--color-bg']);
    expect(metas[1].getAttribute('content')).toBe(DARK_TOKENS['--color-bg']);
  });

  it('system 态恢复亮/暗双声明值', () => {
    seedMetas();
    act(() => useSettingsStore.getState().setTheme('dark'));
    const { unmount } = render(
      <ThemeProvider>
        <p>content</p>
      </ThemeProvider>,
    );
    unmount();
    act(() => useSettingsStore.getState().setTheme('system'));
    render(
      <ThemeProvider>
        <p>content</p>
      </ThemeProvider>,
    );
    const metas = document.querySelectorAll('meta[name="theme-color"]');
    expect(metas[0].getAttribute('content')).toBe(LIGHT_TOKENS['--color-bg']);
    expect(metas[1].getAttribute('content')).toBe(DARK_TOKENS['--color-bg']);
  });
});
